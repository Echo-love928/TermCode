"""主对话用于启动定义式或 Fork 子 Agent 的工具。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from termcode.agent.agent_worktree import _execute_with_worktree
from termcode.agent.fork import build_forked_messages, is_fork_context
from termcode.agent.launch import child_tools, prepare_child
from termcode.agent.team_hook import InProcessTeammateNoSpawnError, TeamSpawnRequest
from termcode.conversation import Conversation
from termcode.permission import Mode, Outcome
from termcode.subagent.definition import MODELS
from termcode.task import BackgroundTask, Manager
from termcode.tool import Result, arguments, string_schema

from .agent import ApprovalRequest, Event

if TYPE_CHECKING:
    from termcode.agent.agent import Agent, Push
    from termcode.agent.team_hook import TeamHook
    from termcode.config import ProviderConfig
    from termcode.subagent import Catalog
    from termcode.worktree import Manager as WorktreeManager

AUTO_BACKGROUND_SECONDS = 120


@dataclass(frozen=True)
class AgentArgs:
    prompt: str
    description: str
    subagent_type: str = ""
    model: str = ""
    run_in_background: bool = False
    name: str = ""
    team_name: str = ""
    plan_mode_required: bool = False


class AgentTool:
    read_only = False

    def __init__(
        self,
        catalog: Catalog,
        task_mgr: Manager,
        parent: Agent | None = None,
        bg_enabled: bool = True,
        *,
        providers: tuple[ProviderConfig, ...] = (),
        worktree_mgr: WorktreeManager | None = None,
        team_hook: TeamHook | None = None,
        teammate_backend: str = "",
    ) -> None:
        self.catalog = catalog
        self.task_mgr = task_mgr
        self.parent = parent
        self.bg_enabled = bg_enabled
        self.providers = providers
        self.worktree_mgr = worktree_mgr
        self.team_hook = team_hook
        self.teammate_backend = teammate_backend
        self._roles = "\n".join(
            f"- {role.name}: {' '.join(role.description.split())}"
            for role in catalog.list()
        )

    def name(self) -> str:
        return "Agent"

    def description(self) -> str:
        description = (
            "把独立任务交给子 Agent。指定 subagent_type 选择角色，"
            "留空则继承当前对话历史并在后台 Fork。后台任务用 TaskList / TaskGet 查询。"
        )
        if self.teammate_backend == "in-process":
            description = "同步委派独立任务，必须指定 subagent_type。"
        if self.team_hook is not None:
            description += (
                "指定 team_name 和 name 可把队员派进团队，在独立 Worktree 后台执行。"
            )
        return description + "可用角色：\n" + self._roles

    def parameters(self) -> dict[str, Any]:
        schema = string_schema(
            {"prompt": "交给子 Agent 的任务", "description": "一句话任务描述"},
            subagent_type="角色名，留空时 Fork",
            model="模型档位，默认沿用角色配置",
            name="后台任务名称，可用于 SendMessage 续派",
        )
        schema["properties"]["model"]["enum"] = list(MODELS)
        schema["properties"]["run_in_background"] = {
            "type": "boolean",
            "description": "立即后台启动并返回任务 ID",
        }
        if self.teammate_backend == "in-process":
            del schema["properties"]["run_in_background"]
        if self.team_hook is not None:
            schema["properties"]["team_name"] = {
                "type": "string",
                "description": "已有团队名称",
            }
            schema["properties"]["plan_mode_required"] = {
                "type": "boolean",
                "description": "队员需先提交计划并等待 Lead 审批",
            }
        return schema

    def set_parent(self, agent: Agent) -> None:
        self.parent = agent

    def _parse(self, args: str) -> AgentArgs:
        data = json.loads(args or "{}")
        if not isinstance(data, dict):
            raise ValueError("参数必须是 JSON 对象")
        background = data.pop("run_in_background", False)
        if not isinstance(background, bool):
            raise ValueError("run_in_background 必须是布尔值")
        plan_required = data.pop("plan_mode_required", False)
        if not isinstance(plan_required, bool):
            raise ValueError("plan_mode_required 必须是布尔值")
        schema = self.parameters()
        values = arguments(json.dumps(data), schema)
        if not values["prompt"].strip() or not values["description"].strip():
            raise ValueError("prompt 和 description 必须为非空字符串")
        model = values.get("model", "").strip()
        if model and model not in MODELS:
            raise ValueError(f"未知子 Agent 模型档位: {model}")
        team_name = values.get("team_name", "")
        if not isinstance(team_name, str):
            raise ValueError("team_name 必须是字符串")
        return AgentArgs(
            values["prompt"],
            values["description"],
            values.get("subagent_type", "").strip(),
            model,
            background,
            values.get("name", "").strip(),
            team_name.strip(),
            plan_required,
        )

    def _model_config(self, parent: Agent, model: str) -> ProviderConfig | None:
        if model == "inherit":
            return None
        pattern = re.compile(rf"(?:^|[^a-z0-9]){model}(?:$|[^a-z0-9])", re.IGNORECASE)
        if pattern.search(parent.provider.model) or pattern.search(
            parent.provider.name
        ):
            return None
        return next(
            (
                config
                for config in self.providers
                if pattern.search(config.name) or pattern.search(config.model)
            ),
            None,
        )

    async def execute(self, args: str) -> Result:
        if self.parent is None:
            return Result("Agent 工具缺少主会话上下文", is_error=True)

        def respond(event: Event | ApprovalRequest) -> None:
            if isinstance(event, ApprovalRequest):
                event.respond.set_result(Outcome.DENY_ONCE)

        return await self.execute_for_turn(
            args, self.parent, Conversation(), Mode.DEFAULT, respond
        )

    async def execute_for_turn(
        self,
        args: str,
        parent: Agent,
        parent_conv: Conversation,
        mode: Mode,
        push: Push,
    ) -> Result:
        if parent.query_source == "fork" or is_fork_context(parent_conv.messages()):
            return Result("Fork 子 Agent 不能再启动 Agent", is_error=True)
        if parent.query_source != "main" and parent.teammate is None:
            return Result("子 Agent 不能再启动 Agent", is_error=True)
        try:
            params = self._parse(args)
            if params.team_name:
                return await self._spawn_teammate(params, parent, parent_conv)
        except (ValueError, RecursionError) as exc:
            return Result(str(exc), is_error=True)
        definition = (
            self.catalog.resolve(params.subagent_type)
            if params.subagent_type
            else self.catalog.fork_definition()
        )
        if definition is None:
            return Result(f"未知 subagent_type: {params.subagent_type}", is_error=True)
        if self.teammate_backend == "in-process" and (
            params.run_in_background or definition.background or definition.is_fork()
        ):
            return Result("进程内队员只能启动同步的定义式子 Agent", is_error=True)
        isolated = definition.isolation == "worktree"
        if isolated and self.worktree_mgr is None:
            return Result("Worktree 功能未启用，无法启动隔离子 Agent", is_error=True)
        fork = definition.is_fork()
        if not isolated and not self.bg_enabled and (params.run_in_background or fork):
            message = "后台禁用，无法 Fork" if fork else "后台子 Agent 已禁用"
            return Result(message, is_error=True)
        background = (
            not isolated
            and self.bg_enabled
            and (fork or definition.background or params.run_in_background)
        )
        conv = (
            Conversation.from_messages(
                build_forked_messages(parent_conv.messages(), params.prompt)
            )
            if fork
            else Conversation()
        )
        child = None
        adopted = False
        try:
            child = await prepare_child(
                parent,
                definition,
                "fork" if fork else "defined",
                params.name or definition.name,
                background=background,
                provider_config=self._model_config(
                    parent, params.model or definition.model
                ),
                permission_mode=mode if fork else None,
            )
            if isolated:
                assert self.worktree_mgr is not None
                return Result(
                    await _execute_with_worktree(
                        self.worktree_mgr, child.agent, conv, params.prompt, push
                    )
                )
            if background:
                task_id = await self.task_mgr.launch(
                    child.agent,
                    conv,
                    params.name,
                    params.prompt,
                    cleanup=child.aclose,
                    prompt="" if fork else None,
                    cwd=parent.engine.root if parent.teammate is not None else "",
                )
                adopted = True
                return _launched(task_id, "async_launched")

            entry = BackgroundTask(
                child.agent, conv, params.name, params.prompt, child.aclose
            )
            handle = self.task_mgr.start_foreground(entry, push)
            detach = asyncio.create_task(entry.detach.wait())
            try:
                await asyncio.wait(
                    (handle, detach),
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=AUTO_BACKGROUND_SECONDS if self.bg_enabled else None,
                )
                if handle.done():
                    return Result(handle.result())
                task_id = self.task_mgr.adopt_running(
                    entry, child_tools(parent, definition, background=True)
                )
                adopted = True
                status = (
                    "detached_to_background"
                    if entry.detach.is_set()
                    else "timed_out_to_background"
                )
                return _launched(task_id, status)
            finally:
                self.task_mgr.foreground = None
                detach.cancel()
                if not adopted and not handle.done():
                    handle.cancel()
                    await asyncio.gather(handle, return_exceptions=True)
        except Exception as exc:
            return Result(f"子 Agent 执行失败：{exc}", is_error=True)
        finally:
            if child is not None and not adopted:
                try:
                    await child.aclose()
                except Exception:
                    logging.getLogger(__name__).warning("无法释放前台子 Agent 的资源")

    async def _spawn_teammate(
        self, params: AgentArgs, parent: Agent, conv: Conversation
    ) -> Result:
        if parent.teammate is not None:
            if self.teammate_backend == "in-process":
                raise InProcessTeammateNoSpawnError("进程内队员不能向团队添加队员")
            raise ValueError("队员不能向团队添加队员")
        if self.team_hook is None:
            raise ValueError("Agent Teams 不可用")
        if not params.name:
            raise ValueError("派出团队队员时必须提供 name")
        if not params.subagent_type and self.team_hook.fork_enabled:
            definition = self.catalog.fork_definition()
        else:
            definition = self.catalog.resolve(params.subagent_type or "general-purpose")
        if definition is None:
            raise ValueError(f"未知 subagent_type: {params.subagent_type}")
        selected = self._model_config(parent, params.model or definition.model)
        try:
            result = await self.team_hook.spawn_teammate(
                TeamSpawnRequest(
                    params.team_name,
                    params.name,
                    params.prompt,
                    definition,
                    params.model,
                    parent,
                    conv,
                    selected,
                    selected.name if selected else parent.provider_name,
                    params.plan_mode_required
                    or definition.permission_mode == Mode.PLAN,
                )
            )
        except (OSError, RuntimeError) as exc:
            return Result(f"无法启动队员：{exc}", is_error=True)
        return Result(json.dumps(result, ensure_ascii=False))


def _launched(task_id: str, status: str) -> Result:
    return Result(json.dumps({"task_id": task_id, "status": status}))
