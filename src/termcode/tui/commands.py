"""斜杠命令注册表与统一压缩提示。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from rich.text import Text

from termcode.agent import NOTICE_CANCELLED, CompactEvent, CompactPhase
from termcode.permission import Mode
from termcode.prompt import EXECUTE_DIRECTIVE

if TYPE_CHECKING:
    from termcode.tui.app import TermCodeApp

CommandHandler = Callable[["TermCodeApp"], Awaitable[None]]


def format_compact_notice(event: CompactEvent) -> str:
    if event.phase == CompactPhase.BEFORE_AUTO:
        return "正在压缩上下文..."
    if event.phase == CompactPhase.BEFORE_EMERGENCY:
        return "上下文撞墙，自动压缩中..."
    if event.err is not None:
        return f"压缩失败：{event.err}"
    return f"已压缩，token 从 {event.before} 降至 {event.after}"


async def handle_exit(app: TermCodeApp) -> None:
    await app.action_quit()


async def handle_plan(app: TermCodeApp) -> None:
    app.mode = Mode.PLAN
    app._write(Text("已进入计划模式（只读工具）；/do 开始执行。", style="dim"))
    app._refresh_statusbar()


async def handle_do(app: TermCodeApp) -> None:
    app.mode = Mode.DEFAULT
    app.conv.add_user(EXECUTE_DIRECTIVE)
    app._begin_turn("/do")


async def handle_compact(app: TermCodeApp) -> None:
    from termcode.tui.app import ChatInput, SessionState

    agent = app._ensure_agent()
    defs = (
        app._tool_registry.read_only_definitions()
        if app.mode == Mode.PLAN
        else app._tool_registry.definitions()
    )
    app._manual_compact = True
    app.state = SessionState.STREAMING
    app.turn_cancel = asyncio.Event()
    app.query_one("#input", ChatInput).read_only = True
    app._write(Text("正在压缩上下文...", style="dim"))

    async def compact() -> None:
        try:
            before, after = await agent.run_force_compact(app.conv, defs)
            result = CompactEvent(CompactPhase.AFTER_AUTO, before, after)
            app._write(Text(app._clean(format_compact_notice(result)), style="dim"))
        except asyncio.CancelledError:
            if not app._quitting:
                app._write(Text(NOTICE_CANCELLED, style="dim"))
            raise
        except Exception as exc:
            result = CompactEvent(CompactPhase.AFTER_AUTO, err=exc)
            app._write(Text(app._clean(format_compact_notice(result)), style="dim"))
        finally:
            app._manual_compact = False
            if not app._quitting:
                app._return_to_idle()

    app._stream_task = asyncio.create_task(compact())


BUILTIN_COMMANDS: dict[str, CommandHandler] = {
    "/exit": handle_exit,
    "/plan": handle_plan,
    "/do": handle_do,
    "/compact": handle_compact,
}


def dispatch_command(input_: str) -> tuple[CommandHandler | None, bool]:
    text = input_.strip()
    if not text.startswith("/"):
        return None, False
    handler = BUILTIN_COMMANDS.get(text)
    if handler is not None:
        return handler, True

    async def unknown_command(app: TermCodeApp) -> None:
        app._write(
            Text(
                app._clean(
                    f"未知命令: {text}，可用命令: " + " ".join(BUILTIN_COMMANDS)
                ),
                style="dim",
            )
        )

    return unknown_command, True
