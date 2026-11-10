"""为不同后端构造同一套队员身份、工具和会话。"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from termcode.agent import Agent
from termcode.agent.agent_tool import AgentTool
from termcode.agent.fork import build_forked_messages
from termcode.agent.team_hook import TeammateContext
from termcode.compact import SessionContext, new_session_context
from termcode.config import effective_context_window
from termcode.conversation import Conversation
from termcode.llm import new_provider
from termcode.permission import Mode
from termcode.session import Writer, load_session
from termcode.tool.filter import FilterParams, apply_agent_tool_filter
from termcode.worktree.slug import validate_slug

from .backend import SpawnRequest
from .mailbox import Box, Message
from .tools import collaboration_tools
from .types import BackendType, Team, TeammateInfo

if TYPE_CHECKING:
    from termcode.agent.team_hook import TeamSpawnRequest
    from termcode.config import ProviderConfig
    from termcode.llm import Provider
    from termcode.subagent import Catalog, Definition

    from .manager import Manager

logger = logging.getLogger(__name__)
TEAM_PROMPT = """
你是团队队员。只在本地写回复，其他队员和用户都看不到。
必须使用 SendMessage 给 Lead 或其他队员同步进度、阻塞和结果。
通过共享任务与其他队员协作，在自己的 Worktree 修改和提交。
需要计划审批时，用 SendMessage 向 lead 发送 plan_approval_request，
在 Lead 批准前保持计划模式。收到 shutdown_request 后，带上原 request_id
用 shutdown_response 明确回复是否同意退出。
""".strip()


def build_team_context_reminder(team: Team, member: TeammateInfo) -> str:
    names = ", ".join(
        f"{m.name}({m.agent_type or ('lead' if m.name == 'lead' else 'fork')})"
        for m in team.members
    )
    return (
        "<team-context>\n"
        f"team: {team.sanitized_name}\n"
        f"你的成员名: {member.name}\n"
        f"你的 agent_id: {member.agent_id}\n"
        f"worktree 目录: {member.worktree_path}\n"
        f"当前团队成员: {names}\n"
        "</team-context>"
    )


def build_member_agent(
    manager: Manager,
    team: Team,
    member: TeammateInfo,
    parent: Agent,
    definition: Definition,
    provider: Provider,
    provider_config: ProviderConfig | None,
    catalog: Catalog,
    providers: tuple[ProviderConfig, ...],
) -> Agent:
    context = TeammateContext(
        team.sanitized_name,
        member.name,
        member.agent_id,
        member.backend_type,
        Box(team.mailbox_dir),
    )
    registry = parent.base_registry.overlay(
        [
            AgentTool(
                catalog,
                manager.task_mgr,
                bg_enabled=member.backend_type != BackendType.IN_PROCESS,
                providers=providers,
                teammate_backend=member.backend_type,
            ),
            *collaboration_tools(
                manager, team_name=team.sanitized_name, teammate=context
            ),
        ]
    )
    allowed = apply_agent_tool_filter(
        FilterParams(
            [d.name for d in registry.definitions()],
            definition.source,
            False,
            definition.tools,
            definition.disallowed_tools,
            definition.is_fork(),
            teammate=True,
        )
    )
    agent = parent.spawn_child(
        provider,
        allowed,
        SessionContext(
            Path(member.session_dir).name,
            str(Path(member.session_dir) / "tool-results"),
            member.session_dir,
        ),
        context_window=(
            effective_context_window(provider_config)
            if provider_config
            else parent.runtime.context_window
        ),
        system_prompt=None if definition.is_fork() else definition.system_prompt,
        max_turns=definition.max_turns,
        permission_mode=Mode.PLAN if member.plan_mode_required else Mode.DEFAULT,
        dont_ask=True,
        query_source="fork" if definition.is_fork() else "defined",
        label=member.name,
        registry=registry,
    )
    agent.engine = replace(parent.engine, root=member.worktree_path, root_valid=True)
    agent.teammate = context
    # 团队身份在每轮续派时仍可见，独立于对话压缩。
    agent.prompt_suffix = (
        f"{TEAM_PROMPT}\n\n{build_team_context_reminder(team, member)}"
    )
    return agent


async def spawn_teammate(
    manager: Manager, team: Team, req: TeamSpawnRequest
) -> dict[str, str]:
    if manager.wt_mgr is None:
        raise ValueError("创建队员需要可用的 Git Worktree")
    name = req.member_name
    if "/" in name or name.casefold() == "lead":
        raise ValueError("无效的队员名称")
    slug = f"team-{team.sanitized_name}/{name}"
    validate_slug(slug)
    if any(m.name.casefold() == name.casefold() for m in team.members):
        raise ValueError(f"团队已有队员 {name}")
    if manager.wt_mgr.get(slug) is not None:
        raise ValueError(f"队员 Worktree 已存在：{slug}")
    parent_tool = req.parent.base_registry.get("Agent")
    if not isinstance(parent_tool, AgentTool):
        raise ValueError("队员缺少 Agent 角色目录")
    agent_id = f"agent-{uuid4().hex[:14]}"
    backend = manager.backend(team.backend)
    wt = await manager.wt_mgr.create(slug, "HEAD", False)
    session = new_session_context(str(manager.root))
    member = TeammateInfo(
        name,
        agent_id,
        "" if req.definition.is_fork() else req.definition.name,
        req.model,
        wt.path,
        wt.branch,
        team.backend,
        is_active=None,
        plan_mode_required=req.plan_mode_required,
        session_dir=session.session_dir,
    )
    request = SpawnRequest(
        team.sanitized_name,
        name,
        agent_id,
        wt.path,
        session.session_dir,
        member.agent_type,
        member.model,
        req.prompt,
        req.plan_mode_required,
        str(manager.root),
        req.provider_name,
    )
    writer: Writer | None = None
    owned: Provider | None = None
    reserved = False
    started = False

    async def close() -> None:
        try:
            if writer is not None:
                writer.close()
        finally:
            if owned is not None:
                await owned.aclose()

    def restore() -> Conversation:
        assert writer is not None
        return Conversation.from_messages(
            load_session(session.session_dir),
            writer.on_append,
            writer.on_replace,
        )

    try:
        # 先落成员身份和初始消息，避免子进程启动后查不到自己。
        await team.add_member(member)
        reserved = True
        if team.backend == BackendType.IN_PROCESS or req.definition.is_fork():
            writer = Writer(session.session_dir, req.parent.provider.model)
            if req.definition.is_fork():
                writer.append_all(
                    build_forked_messages(req.conversation.messages(), "")[:-1]
                )
        if team.backend == BackendType.IN_PROCESS:
            if req.provider_config is not None:
                owned = new_provider(req.provider_config)
                assert writer is not None
                writer.model = owned.model
            request.sub_agent = build_member_agent(
                manager,
                team,
                member,
                req.parent,
                req.definition,
                owned or req.parent.provider,
                req.provider_config,
                parent_tool.catalog,
                parent_tool.providers,
            )
            request.conv = restore()
            request.cleanup = close
            request.restore = restore
        else:
            if writer is not None:
                writer.close()
                writer = None
            await Box(team.mailbox_dir).write(agent_id, Message("lead", req.prompt))
        pane_id, returned_id = await backend.spawn(request)
        started = True
        member = replace(member, pane_id=pane_id)
        if returned_id != agent_id:
            raise RuntimeError("队员后端返回了不匹配的 Agent ID")
        if pane_id:
            await team.update_member(agent_id, pane_id=pane_id)
        manager.registry.register(name, agent_id)
    except BaseException:
        try:
            if started:
                await backend.kill(member.pane_id, agent_id)
            else:
                await close()
            if reserved:
                await team.remove_member(name)
            await manager.cleanup_member(team, member)
        except Exception:
            logger.exception("队员 %s 启动失败后清理资源失败", name)
        raise
    return {
        "member_name": name,
        "agent_id": agent_id,
        "worktree": wt.path,
        "backend": team.backend,
        "pane_id": member.pane_id,
    }
