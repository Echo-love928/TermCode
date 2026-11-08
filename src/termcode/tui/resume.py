"""会话选择弹窗与恢复提交。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static

from termcode.agent import SessionRuntime
from termcode.compact import open_session_context
from termcode.compact.const import AUTO_SAFETY_MARGIN, SUMMARY_RESERVE
from termcode.compact.token import estimate_tokens
from termcode.conversation import Conversation
from termcode.hook import Event as HookEvent
from termcode.session import (
    SessionInfo,
    Writer,
    last_timestamp,
    list_sessions,
    load_session,
    load_usage,
)

if TYPE_CHECKING:
    from termcode.tui.app import TermCodeApp


@dataclass
class SessionItem:
    info: SessionInfo

    @property
    def display_text(self) -> str:
        hours = max(0, int((time.time() - self.info.modified_at) / 3600))
        ago = f"{hours // 24} days ago" if hours >= 24 else f"{hours} hours ago"
        return (
            f"{self.info.title} · {ago} · {self.info.model} · "
            f"{self.info.size / 1024:.1f}KB"
        )


class ResumeScreen(ModalScreen[str | None]):
    DEFAULT_CSS = """
    ResumeScreen { padding: 1 2; background: $surface; }
    #resume-title { height: auto; margin-bottom: 1; }
    #resume-search { height: 3; }
    #resume-options { height: 1fr; border: round $accent; }
    """
    BINDINGS = [("escape", "dismiss(None)", "取消")]

    def __init__(self, items: list[SessionInfo]) -> None:
        super().__init__()
        self.items = items
        self.filtered = items

    def compose(self) -> ComposeResult:
        yield Static(
            "恢复会话 · 输入搜索 · ↑/↓ 选择 · Enter 恢复 · Esc 取消", id="resume-title"
        )
        yield Input(placeholder="搜索会话标题", id="resume-search")
        yield OptionList(id="resume-options")

    def on_mount(self) -> None:
        self.filter_items("")
        self.query_one(Input).focus()

    def filter_items(self, query: str) -> None:
        self.filtered = [
            i for i in self.items if query.casefold() in i.title.casefold()
        ]
        options = self.query_one(OptionList)
        options.clear_options()
        options.add_options(
            [Text(SessionItem(info).display_text) for info in self.filtered]
        )
        options.highlighted = 0 if self.filtered else None
        self.query_one("#resume-title", Static).update(
            f"恢复会话 · {len(self.filtered)} 项 · ↑/↓ 选择 · Enter 恢复 · Esc 取消"
        )

    @on(Input.Changed)
    def search(self, event: Input.Changed) -> None:
        self.filter_items(event.value)

    def on_key(self, event: events.Key) -> None:
        options = self.query_one(OptionList)
        if event.key in {"up", "down"}:
            event.stop()
            event.prevent_default()
            if event.key == "up":
                options.action_cursor_up()
            else:
                options.action_cursor_down()

    @on(Input.Submitted)
    def choose_input(self) -> None:
        index = self.query_one(OptionList).highlighted
        if index is not None and self.filtered:
            self.dismiss(self.filtered[index].id)

    @on(OptionList.OptionSelected)
    def choose_option(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.filtered[event.option_index].id)


async def open_resume_menu(app: TermCodeApp) -> None:
    from termcode.tui.app import SessionState

    try:
        items = await asyncio.to_thread(list_sessions, app.sessions_dir)
    except OSError:
        app.state = SessionState.IDLE
        app._write(Text("无法读取会话目录", style="dim"))
        return

    def selected(session_id: str | None) -> None:
        if session_id is None:
            app.state = SessionState.IDLE
            app.query_one("#input").focus()
            return
        info = next(info for info in items if info.id == session_id)
        app._stream_task = asyncio.create_task(do_resume_session(app, info))

    app.push_screen(ResumeScreen(items), selected)


async def do_resume_session(app: TermCodeApp, info: SessionInfo) -> None:
    from termcode.tui.app import SessionState

    writer = None
    app._write(Text("正在恢复会话...", style="dim"))
    try:
        provider = app.provider
        if provider is None:
            raise RuntimeError("尚未选择供应商")
        async with app.runtime.run_lock:
            context = open_session_context(app.workspace, info.id)
            msgs = await asyncio.to_thread(load_session, context.session_dir)
            stamp = await asyncio.to_thread(last_timestamp, context.session_dir)
            usage = await asyncio.to_thread(load_usage, context.session_dir)
            conv = Conversation.from_messages(msgs)
            runtime = SessionRuntime(
                session=context, context_window=app.runtime.context_window
            )
            agent = app._create_agent(provider, runtime)
            threshold = runtime.context_window - SUMMARY_RESERVE - AUTO_SAFETY_MARGIN
            compacted = False
            if conv.length() and estimate_tokens(0, msgs, 0) > threshold:
                await agent.run_force_compact(
                    conv, app._tool_registry.definitions(), app.mode
                )
                compacted = True
            writer = Writer.open_existing(context.session_dir, provider.model)
            if compacted:
                writer.replace_all(conv.messages())
            conv = Conversation.from_messages(
                conv.messages(), writer.on_append, writer.on_replace
            )
            if stamp and time.time() - stamp > 6 * 3600:
                hours = int((time.time() - stamp) / 3600)
                conv.add_user(
                    f"[系统提示] 本会话已暂停 {hours} 小时。部分上下文可能已过时，"
                    "如需最新信息请重新读取相关文件。"
                )
            old_writer = app.writer
            await app.end_hook_session()
            await app._clear_tasks()
            app.conv, app.writer, app.runtime = conv, writer, runtime
            app._bind_agent(agent)
            writer = None
            app.usage_in, app.usage_out = usage
            app._refresh_statusbar()
            if old_writer:
                old_writer.close()
            await app._open_hook_session(HookEvent.SESSION_RESUME)
            for msg in conv.messages():
                if msg.content:
                    app._write(Text(app._clean(f"{msg.role}: {msg.content}")))
            app._write(Text(f"已恢复会话 {info.id}，共 {conv.length()} 条消息"))
    except Exception as exc:
        app._write(Text(app._clean(f"恢复失败，当前会话未切换：{exc}")))
    finally:
        if writer:
            writer.close()
        if not app._quitting:
            app.state = SessionState.IDLE
            app.query_one("#input").focus()
        app._stream_task = None
