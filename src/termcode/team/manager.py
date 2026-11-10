"""团队生命周期、同团队寻址和跨后端通知。"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from termcode.task import Manager as TaskManager
from termcode.task.manager import Status
from termcode.worktree import ExitOptions

from .backend import Backend, new_backend
from .backend.detect import detect
from .mailbox import Box, Message, MessageType
from .registry import AgentNameRegistry
from .types import BackendType, Team, TeammateInfo, sanitize

if TYPE_CHECKING:
    from termcode.agent.team_hook import TeamSpawnRequest
    from termcode.tool import Registry
    from termcode.worktree import Manager as WorktreeManager

logger = logging.getLogger(__name__)
MAIL_PROMPT = "处理团队邮箱中的新消息，结合已有对话继续任务。"


class TeamNotFoundError(ValueError):
    pass


class TeamHasActiveMembersError(ValueError):
    pass


@dataclass(frozen=True)
class LeadMessage:
    team_name: str
    message: Message


class Manager:
    def __init__(
        self,
        home: Path,
        root: Path | str,
        wt_mgr: WorktreeManager | None,
        task_mgr: TaskManager,
        registry: AgentNameRegistry,
        *,
        fork_enabled: bool = False,
        member_process: bool = False,
    ) -> None:
        self.root = Path(root).resolve()
        self.home_dir = Path(home)
        self.directory = self.home_dir / ".termcode/teams"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.wt_mgr = wt_mgr
        self.task_mgr = task_mgr
        self.registry = registry
        self.fork_enabled = fork_enabled
        self._lock = asyncio.Lock()
        self._operations: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.teams: dict[str, Team] = {}
        self.active_team = ""
        for path in self.directory.iterdir():
            if (
                path.name.startswith(".deleted-")
                or path.is_symlink()
                or not path.is_dir()
            ):
                continue
            try:
                team = Team.load(path)
            except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
                logger.warning("跳过损坏的团队配置 %s：%s", path, exc)
                continue
            self.teams[team.sanitized_name] = team
            if not member_process:
                for member in team.members:
                    if member.agent_id != team.lead_agent_id:
                        self.registry.register(member.name, member.agent_id)
        if not member_process:
            task_mgr.set_name_registry(registry)
            task_mgr.on_task_done(self.handle_task_done)

    def backend(self, kind: BackendType) -> Backend:
        return new_backend(kind, self.task_mgr)

    def operation(self, team: Team) -> asyncio.Lock:
        return self._operations[team.sanitized_name]

    def get(self, name: str) -> Team | None:
        try:
            return self.teams.get(sanitize(name))
        except ValueError:
            return None

    def require(self, name: str) -> Team:
        team = self.get(name)
        if team is None:
            raise TeamNotFoundError(f"找不到团队 {name}")
        team.refresh()
        return team

    def owns(self, team: Team) -> bool:
        if team.project_root:
            return team.project_root == str(self.root)
        roots = {
            Path(m.worktree_path).parent.parent.parent
            for m in team.members
            if m.worktree_path
        }
        return not roots or roots == {self.root}

    def require_owned(self, name: str) -> Team:
        team = self.require(name)
        if not self.owns(team):
            raise ValueError("该团队属于另一个项目，请在对应项目中操作")
        return team

    def list_(self) -> list[Team]:
        result = []
        for team in list(self.teams.values()):
            try:
                team.refresh()
            except (OSError, ValueError, KeyError, TypeError):
                continue
            result.append(team)
        return result

    async def create(
        self, name: str, agent_type: str = "", *, description: str = ""
    ) -> Team:
        base = sanitize(name)
        if base.startswith(".deleted-"):
            raise ValueError("无效的团队名称")
        backend = detect()
        async with self._lock:
            suffix = 1
            while True:
                key = base if suffix == 1 else f"{base}-{suffix}"
                path = self.directory / key
                try:
                    path.mkdir(mode=0o700)
                except FileExistsError:
                    suffix += 1
                    continue
                break
            team = Team(
                name,
                key,
                description,
                "lead",
                [TeammateInfo("lead", "lead", backend_type=backend)],
                path,
                datetime.now(UTC),
                backend,
                str(self.root),
            )
            try:
                team.mailbox_dir.mkdir(mode=0o700)
                team.save(team.members)
            except BaseException:
                shutil.rmtree(path, ignore_errors=True)
                raise
            self.teams[key] = team
            self.active_team = key
        return team

    async def recover(self) -> None:
        teams = [team for team in self.list_() if self.owns(team)]
        if len(teams) == 1:
            self.active_team = teams[0].sanitized_name
        for team in teams:
            for member in team.members:
                if member.agent_id == team.lead_agent_id or member.is_active is False:
                    continue
                try:
                    if not await self.backend(member.backend_type).alive(
                        member.pane_id, member.agent_id
                    ):
                        await team.update_member(member.agent_id, is_active=False)
                except (OSError, RuntimeError) as exc:
                    logger.warning("无法核对队员 %s 的状态：%s", member.name, exc)

    def protected_sessions(self) -> frozenset[str]:
        return frozenset(
            member.session_dir
            for team in self.list_()
            for member in team.members
            if member.session_dir
        )

    async def spawn_teammate(self, req: TeamSpawnRequest) -> dict[str, str]:
        from .spawn import spawn_teammate

        team = self.require_owned(req.team_name)

        async def start() -> dict[str, str]:
            async with self.operation(team):
                self.require_owned(team.sanitized_name)
                result = await spawn_teammate(self, team, req)
                self.active_team = team.sanitized_name
                return result

        # 调用者取消时也让创建流程完成提交或回滚。
        worker = asyncio.create_task(start())
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            await asyncio.gather(worker, return_exceptions=True)
            raise

    async def delete(self, name: str, force: bool = False) -> None:
        team = self.require_owned(name)
        async with self.operation(team):
            team = self.require_owned(name)
            members = [m for m in team.members if m.agent_id != team.lead_agent_id]
            if not force and any(m.is_active is not False for m in members):
                raise TeamHasActiveMembersError("仍有活跃队员，请等待或使用 --force")
            for member in members:
                await self.backend(member.backend_type).kill(
                    member.pane_id, member.agent_id
                )
                await self.cleanup_member(team, member)
                self.registry.unregister(member.name, member.agent_id)
            async with self._lock:
                # 先原子移走目录，避免队员把已删除的团队重新写回。
                retired = self.directory / f".deleted-{uuid4().hex}"
                team.config_dir.rename(retired)
                self.teams.pop(team.sanitized_name)
                if self.active_team == team.sanitized_name:
                    self.active_team = ""
            await asyncio.to_thread(shutil.rmtree, retired)

    async def kill(self, target: str) -> TeammateInfo:
        matches = [
            (team, member)
            for team in self.list_()
            if self.owns(team)
            and (member := team.member(target)) is not None
            and member.agent_id != "lead"
        ]
        if not matches:
            raise ValueError(f"找不到队员 {target}")
        if len(matches) > 1:
            raise ValueError("多个团队有同名队员，请指定 agent_id")
        team, member = matches[0]
        async with self.operation(team):
            team = self.require(team.sanitized_name)
            if team.member(member.agent_id) is None:
                raise ValueError(f"队员已退出：{target}")
            await self.backend(member.backend_type).kill(
                member.pane_id, member.agent_id
            )
            await self.cleanup_member(team, member)
            await team.remove_member(member.name)
            self.registry.unregister(member.name, member.agent_id)
        return member

    async def cleanup_member(self, team: Team, member: TeammateInfo) -> None:
        slug = f"team-{team.sanitized_name}/{member.name}"
        if self.wt_mgr is not None:
            wt = self.wt_mgr.get(slug)
            if wt is not None and wt.path == member.worktree_path:
                try:
                    await self.wt_mgr.remove(slug, ExitOptions(discard_changes=True))
                except (OSError, RuntimeError, ValueError) as exc:
                    logger.warning("无法清理队员 %s 的 Worktree：%s", member.name, exc)
        if member.session_dir:
            sessions = (self.root / ".termcode/sessions").resolve()
            path = Path(member.session_dir)
            if await asyncio.to_thread(_is_session_directory, path, sessions):
                try:
                    await asyncio.to_thread(shutil.rmtree, path)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    logger.warning("无法清理队员 %s 的会话：%s", member.name, exc)
        await Box(team.mailbox_dir).remove(member.agent_id)

    def lead_registry(
        self, registry: Registry, *, coordinator: bool = False
    ) -> Registry:
        from .tools import collaboration_tools

        if not (
            self.active_team
            or coordinator
            or any(self.owns(t) for t in self.teams.values())
        ):
            return registry
        return registry.overlay(collaboration_tools(self, team_name=self.active_team))

    async def resume(self, team: Team, member: TeammateInfo) -> None:
        task = self.task_mgr.get(member.agent_id)
        if task is None:
            raise ValueError(f"进程内队员 {member.name} 已退出，请重新派出队员")
        if task.sub_agent.teammate and task.sub_agent.teammate.shutdown_requested:
            raise ValueError(f"队员 {member.name} 已确认退出")
        if task.status == Status.RUNNING or self.task_mgr.clearing:
            return
        await team.update_member(member.agent_id, is_active=True)
        try:
            await self.task_mgr.send_message(
                member.agent_id, MAIL_PROMPT, teammate=True
            )
        except BaseException:
            await team.update_member(member.agent_id, is_active=False)
            raise

    async def handle_task_done(self, agent_id: str) -> None:
        for team in self.list_():
            member = team.member(agent_id)
            if member is None or member.backend_type != BackendType.IN_PROCESS:
                continue
            async with self.operation(team):
                task = self.task_mgr.get(agent_id)
                if task is None or task.status == Status.RUNNING:
                    return
                await self.mark_idle(team, member)
                if (
                    task.status == Status.COMPLETED
                    and not self.task_mgr.clearing
                    and not (
                        task.sub_agent.teammate
                        and task.sub_agent.teammate.shutdown_requested
                    )
                    and (await Box(team.mailbox_dir).read_unread(agent_id))[0]
                ):
                    # 新消息可能在上一轮模型的最后一次请求期间写入。
                    await self.resume(team, member)
            return

    async def mark_idle(self, team: Team, member: TeammateInfo) -> None:
        await team.update_member(member.agent_id, is_active=False)
        await Box(team.mailbox_dir).write(
            team.lead_agent_id,
            Message(member.name, f"[idle] {member.name} (reason: available)"),
        )

    async def poll_lead_mailboxes(self) -> list[LeadMessage]:
        messages: list[LeadMessage] = []
        for team in self.list_():
            if not self.owns(team):
                continue
            box = Box(team.mailbox_dir)
            try:
                indices, unread = await box.read_unread(team.lead_agent_id)
                await box.mark_read(team.lead_agent_id, indices)
            except (OSError, ValueError, TimeoutError, KeyError, TypeError) as exc:
                logger.warning(
                    "无法读取团队 %s 的 Lead 邮箱：%s", team.sanitized_name, exc
                )
                continue
            messages.extend(LeadMessage(team.sanitized_name, msg) for msg in unread)
        return messages

    async def deliver(
        self,
        team: Team,
        actor: str,
        to: str,
        message: Message,
        *,
        member_process: bool = False,
    ) -> list[str]:
        async with self.operation(team):
            team.refresh()
            sender = team.member(actor)
            if sender is None:
                raise ValueError("调用者不属于该团队")
            target = team.member(to)
            if target is None and (agent_id := self.registry.resolve(to)):
                target = team.member(agent_id)
            if to == "*":
                recipients = [m for m in team.members if m.agent_id != actor]
            elif target is not None:
                recipients = [target]
            else:
                raise ValueError(f"团队中找不到收件人 {to}")
            self._validate_message(team, sender, recipients, message)
            box = Box(team.mailbox_dir)
            if message.type == MessageType.PLAN_APPROVAL_RESPONSE:
                await self._validate_plan_response(box, team, recipients[0], message)
            if message.type == MessageType.SHUTDOWN_RESPONSE:
                requests = await box.read(sender.agent_id)
                if not any(
                    m.type == MessageType.SHUTDOWN_REQUEST
                    and m.request_id == message.request_id
                    and m.from_ == "lead"
                    for m in requests
                ):
                    raise ValueError("找不到对应的 Lead 退出请求")
            delivered, failures = [], []
            for member in recipients:
                try:
                    await self._deliver_one(team, box, member, message, member_process)
                    delivered.append(member.agent_id)
                except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
                    failures.append(f"{member.name}：{exc}")
            if failures:
                prefix = f"已送达 {', '.join(delivered)}；" if delivered else ""
                raise ValueError(prefix + "发送失败：" + "；".join(failures))
            return delivered

    async def _deliver_one(
        self,
        team: Team,
        box: Box,
        member: TeammateInfo,
        message: Message,
        member_process: bool,
    ) -> None:
        if member.agent_id == team.lead_agent_id:
            await box.write(member.agent_id, message)
            return
        backend = self.backend(member.backend_type)
        if member.backend_type == BackendType.IN_PROCESS and member_process:
            # Pane 队员没有 Lead 进程的 TaskManager。Lead 轮询时唤醒进程内队员。
            await box.write(member.agent_id, message)
            return
        if not await backend.alive(member.pane_id, member.agent_id):
            await team.update_member(member.agent_id, is_active=False)
            raise ValueError("队员已退出，请重新派出队员")
        await box.write(member.agent_id, message)
        if member.backend_type == BackendType.IN_PROCESS:
            await self.resume(team, member)
        else:
            try:
                await backend.wake(member.pane_id, member.agent_id)
            except (OSError, RuntimeError) as exc:
                raise ValueError(f"消息已写入邮箱，唤醒失败：{exc}") from exc

    @staticmethod
    def _validate_message(
        team: Team,
        sender: TeammateInfo,
        recipients: list[TeammateInfo],
        message: Message,
    ) -> None:
        lead = sender.agent_id == team.lead_agent_id
        targets_lead = (
            len(recipients) == 1 and recipients[0].agent_id == team.lead_agent_id
        )
        kind = message.type
        if message.from_ != sender.name:
            raise ValueError("发件人和调用者不一致")
        if kind in {
            MessageType.PLAN_APPROVAL_REQUEST,
            MessageType.SHUTDOWN_RESPONSE,
        } and (lead or not targets_lead):
            raise ValueError("此消息只能由队员发给 Lead")
        if kind in {
            MessageType.PLAN_APPROVAL_RESPONSE,
            MessageType.SHUTDOWN_REQUEST,
        } and (
            not lead
            or not recipients
            or any(m.agent_id == team.lead_agent_id for m in recipients)
        ):
            raise ValueError("此消息只能由 Lead 发给队员")
        if kind == MessageType.PLAN_APPROVAL_RESPONSE and len(recipients) != 1:
            raise ValueError("每次审批只能回复一位队员")
        if kind in {
            MessageType.PLAN_APPROVAL_RESPONSE,
            MessageType.SHUTDOWN_RESPONSE,
        } and (not message.request_id or message.approve is None):
            raise ValueError("应答必须提供 request_id 和 approve")

    @staticmethod
    async def _validate_plan_response(
        box: Box, team: Team, member: TeammateInfo, message: Message
    ) -> None:
        requests = [
            m
            for m in await box.read(team.lead_agent_id)
            if m.type == MessageType.PLAN_APPROVAL_REQUEST and m.from_ == member.name
        ]
        if (
            not message.request_id.startswith(f"req-{member.agent_id}-")
            or not requests
            or requests[-1].request_id != message.request_id
        ):
            raise ValueError("request_id 与队员最新提交的计划不匹配")
        if any(
            m.type == MessageType.PLAN_APPROVAL_RESPONSE
            and m.request_id == message.request_id
            for m in await box.read(member.agent_id)
        ):
            raise ValueError("该计划已审批")

    async def wake_pending(self) -> None:
        for team in self.list_():
            if not self.owns(team) or self.operation(team).locked():
                continue
            async with self.operation(team):
                for member in team.members:
                    if (
                        member.agent_id != team.lead_agent_id
                        and member.backend_type == BackendType.IN_PROCESS
                        and self.task_mgr.get(member.agent_id) is not None
                        and (await Box(team.mailbox_dir).read_unread(member.agent_id))[
                            0
                        ]
                    ):
                        with suppress(ValueError, FileNotFoundError):
                            await self.resume(team, member)


def _is_session_directory(path: Path, sessions: Path) -> bool:
    return not path.is_symlink() and path.resolve().parent == sessions
