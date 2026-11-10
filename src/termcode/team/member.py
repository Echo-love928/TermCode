"""窗格队员的命令行入口。界面只输出 Agent 事件，回车用于唤醒邮箱。"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from contextlib import suppress
from pathlib import Path
from threading import Thread

from termcode import __version__, hook
from termcode.agent import Agent, ApprovalRequest, Event, Phase, SessionRuntime
from termcode.config import Config, effective_context_window
from termcode.conversation import Conversation
from termcode.instructions import Loader
from termcode.llm import error_message, new_provider
from termcode.mcp import load_config, new_manager
from termcode.permission import Outcome, new_engine
from termcode.session import Writer, load_session
from termcode.subagent import load_catalog
from termcode.task import Manager as TaskManager
from termcode.tool import new_default_registry, with_cwd

from .mailbox import Box
from .manager import MAIL_PROMPT, Manager
from .registry import AgentNameRegistry
from .spawn import build_member_agent
from .types import Team, TeammateInfo


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="termcode --team-member")
    for name in ("team", "member", "agent-id", "session-dir", "worktree"):
        parser.add_argument(f"--{name}", required=True)
    for name in ("agent-type", "model", "provider"):
        parser.add_argument(f"--{name}", default="")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--plan-mode", action="store_true")
    return parser.parse_args(argv)


def _wake_on_enter(wake: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    def read() -> None:
        try:
            for _ in sys.stdin:
                loop.call_soon_threadsafe(wake.set)
        except (OSError, RuntimeError, ValueError):
            pass

    # 即便 stdin 没有 EOF，进程也可以退出；邮箱另有定时兜底。
    Thread(target=read, daemon=True).start()


def _print_event(event: Event | ApprovalRequest) -> None:
    if isinstance(event, ApprovalRequest):
        event.respond.set_result(Outcome.DENY_ONCE)
    elif event.tool and event.tool.phase == Phase.START:
        print(f"● {event.tool.name}({event.tool.args})", flush=True)
    elif event.tool and event.tool.is_error:
        print(f"  {event.tool.result[:500]}", flush=True)
    elif event.text:
        print(event.text, end="", flush=True)
    elif event.done:
        print("\n────────────────", flush=True)


async def run_team_member(args: argparse.Namespace, config: Config) -> None:
    root = args.project_root.resolve()
    manager = Manager(
        Path.home(),
        root,
        None,
        TaskManager(),
        AgentNameRegistry(),
        member_process=True,
    )
    team = manager.require_owned(args.team)
    member = team.member(args.agent_id)
    if (
        member is None
        or member.name != args.member
        or member.worktree_path != args.worktree
        or member.session_dir != args.session_dir
        or member.agent_type != args.agent_type
        or member.plan_mode_required != args.plan_mode
        or member.model != args.model
        or member.agent_id == team.lead_agent_id
    ):
        raise ValueError("队员启动参数与团队配置不一致")
    worktree, session_dir, worktrees_root, sessions_root = await asyncio.to_thread(
        lambda: (
            Path(member.worktree_path).resolve(),
            Path(member.session_dir).resolve(),
            (root / ".termcode/worktrees").resolve(),
            (root / ".termcode/sessions").resolve(),
        )
    )
    if worktree.parent != worktrees_root or session_dir.parent != sessions_root:
        raise ValueError("队员 Worktree 或会话目录越界")
    os.chdir(worktree)

    catalog = load_catalog(root)
    definition = (
        catalog.resolve(member.agent_type)
        if member.agent_type
        else catalog.fork_definition()
    )
    if definition is None:
        raise ValueError(f"未知队员角色：{member.agent_type}")
    selected = next(
        (provider for provider in config.providers if provider.name == args.provider),
        config.providers[0],
    )
    provider = new_provider(selected)
    hooks = hook.load(str(root))
    try:
        mcp = await new_manager(load_config(str(root)), __version__)
        try:
            registry = new_default_registry()
            mcp_tools = mcp.tools()
            for tool in mcp_tools:
                registry.register(tool)
            engine, error = new_engine(str(root))
            if error is not None:
                raise ValueError("无法解析队员的项目根目录")
            engine.allow_temp_files = True
            engine.mcp_tools = frozenset(tool.name() for tool in mcp_tools)
            parent = Agent(
                provider,
                registry,
                engine=engine,
                runtime=SessionRuntime(
                    context_window=effective_context_window(selected)
                ),
                instruction_text=Loader(str(root)).load(),
                hook_engine=hooks if hooks.rules else None,
            )
            agent = build_member_agent(
                manager,
                team,
                member,
                parent,
                definition,
                provider,
                selected,
                catalog,
                tuple(config.providers),
            )
            with Writer(member.session_dir, provider.model) as writer:
                conv = Conversation.from_messages(
                    load_session(member.session_dir),
                    writer.on_append,
                    writer.on_replace,
                )
                print(
                    f"[team-member] {member.name} · team={team.sanitized_name} "
                    f"· agent={member.agent_id} · cwd={worktree}",
                    flush=True,
                )
                with with_cwd(member.worktree_path):
                    await _run_mailbox(manager, team, member, agent, conv)
        finally:
            await manager.task_mgr.clear()
            await mcp.close()
    finally:
        await hooks.aclose()
        await provider.aclose()


async def _run_mailbox(
    manager: Manager, team: Team, member: TeammateInfo, agent: Agent, conv: Conversation
) -> None:
    wake = asyncio.Event()
    _wake_on_enter(wake)
    box = Box(team.mailbox_dir)
    assert agent.teammate is not None
    while team.mailbox_dir.is_dir() and not agent.teammate.shutdown_requested:
        wake.clear()
        indices, _ = await box.read_unread(member.agent_id)
        if not indices:
            with suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), timeout=2)
            continue
        try:
            await team.update_member(member.agent_id, is_active=True)
            await agent.run_to_completion(conv, MAIL_PROMPT, push=_print_event)
        except Exception as exc:
            print(error_message(exc), file=sys.stderr, flush=True)
        finally:
            with suppress(FileNotFoundError):
                await manager.mark_idle(team, member)
        # 邮箱短时不可用时，避免无间隔重试。
        if (await box.read_unread(member.agent_id))[0] == indices:
            await asyncio.sleep(1)
