"""启动选择、输入、流式输出和退出组成的终端状态机。"""

import asyncio
import os
import time
from contextlib import suppress
from dataclasses import replace
from enum import Enum

from rich.console import Console, RenderableType
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message as UIMessage
from textual.timer import Timer
from textual.widgets import OptionList, RichLog, Static, TextArea

from termcode import __version__
from termcode.agent import ApprovalRequest, ToolEvent
from termcode.config import ProviderConfig
from termcode.conversation import Conversation
from termcode.llm import Provider, new_provider
from termcode.permission import Engine, Mode, Outcome, new_engine
from termcode.prompt import EXECUTE_DIRECTIVE, StartupBanner
from termcode.tool import Registry, new_default_registry
from termcode.tui.banner import (
    BannerArtwork,
    TranscriptLog,
    TranscriptViewport,
    graphics_available,
    print_banner_image,
)
from termcode.tui.select import provider_list
from termcode.tui.stream import consume_stream, tick, update_approving
from termcode.tui.view import (
    approval_block,
    clean_text,
    error_block,
    error_message,
    render_markdown,
    status_bar,
    user_block,
)


class SessionState(Enum):
    SELECTING = "selecting"
    IDLE = "idle"
    STREAMING = "streaming"
    APPROVING = "approving"


def next_mode(mode: Mode) -> Mode:
    return Mode((int(mode) + 1) % len(Mode))


class ChatInput(TextArea):
    class Submitted(UIMessage):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def on_key(self, event: events.Key) -> None:
        if event.key == "shift+tab":
            # 交给应用级模式绑定，不让编辑器将其解释为反向缩进。
            return
        # 与普通字符在同一控件队列中处理，避免优先绑定抢在粘贴文本之前执行。
        if event.key in {"enter", "alt+enter"}:
            event.stop()
            event.prevent_default()
            if event.key == "alt+enter":
                self.action_newline()
            else:
                self.action_send()

    def action_send(self) -> None:
        if not self.read_only and self.text.strip():
            self.post_message(self.Submitted(self.text))

    def action_newline(self) -> None:
        if not self.read_only:
            self.insert("\n")


class TermCodeApp(App[None]):
    TITLE = "TermCode"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("ctrl+c", "interrupt", "取消或退出", priority=True),
        Binding("escape", "cancel_turn", "取消本轮", priority=True),
        Binding("shift+tab", "next_mode", "切换权限模式", priority=True),
        Binding("pageup", "history_up", "向上翻页", priority=True),
        Binding("pagedown", "history_down", "向下翻页", priority=True),
    ]
    CSS = """
    Screen { height: 32; min-height: 10; max-height: 100%; background: $surface; }
    #transcript { width: 1fr; height: 1fr; min-height: 2; }
    #log { width: 1fr; height: auto; min-height: 1; padding: 0 1;
           overflow: hidden hidden; }
    #selection-title { height: auto; margin: 1; color: $text; }
    #providers { height: 1fr; margin: 0 1; border: round $accent; }
    #streaming-scroll { height: auto; max-height: 45%; padding: 0 1; }
    #streaming { height: auto; width: 1fr; }
    #composer { height: 5; margin: 0 1; border: round $primary; }
    #composer:focus-within { border: round $accent; }
    #prompt { width: 3; height: 1; color: $accent; }
    #input { width: 1fr; height: 3; border: none; padding: 0; }
    #input:focus { border: none; }
    #statusbar { height: 1; width: 1fr; }
    """

    def __init__(
        self,
        providers: list[ProviderConfig],
        registry: Registry | None = None,
        engine: Engine | None = None,
    ) -> None:
        super().__init__()
        if not providers:
            raise ValueError("至少需要一项供应商配置")
        self.providers = providers
        self.provider: Provider | None = None
        self.state = SessionState.SELECTING
        self.conv = Conversation()
        self._tool_registry = (
            registry if registry is not None else new_default_registry()
        )
        self.cur_tools: list[ToolEvent] = []
        self.engine = engine if engine is not None else new_engine(os.getcwd())[0]
        self.mode = self.engine.start_mode()
        self.pending: ApprovalRequest | None = None
        self.approve_cursor = 0
        self.iter = 0
        self.usage_in = 0
        self.usage_out = 0
        self.turn_cancel: asyncio.Event | None = None
        self.cur_reply = ""
        self.turn_start = 0.0
        self._stream_task: asyncio.Task[None] | None = None
        self._timer: Timer | None = None
        self._quitting = False
        self._provider_closed = False
        self._transcript: list[RenderableType] = []
        self._secrets = [cfg.api_key for cfg in providers]

    def compose(self) -> ComposeResult:
        yield Static("选择供应商 · ↑ / ↓ 移动 · Enter 确认", id="selection-title")
        yield provider_list(self.providers)
        with TranscriptViewport(id="transcript"):
            yield BannerArtwork(id="startup-artwork")
            yield TranscriptLog(
                id="log", wrap=True, min_width=1, markup=False, auto_scroll=False
            )
        with VerticalScroll(id="streaming-scroll"):
            yield Static("", id="streaming", markup=False)
        with Horizontal(id="composer"):
            yield Static("❯", id="prompt")
            yield ChatInput(id="input", placeholder="Send a message...")
        yield Static("", id="statusbar", markup=False)

    def on_mount(self) -> None:
        self._write(
            StartupBanner(
                self._clean(__version__),
                self._clean(os.getcwd()),
                show_name=not graphics_available(),
            )
        )
        self.query_one("#streaming-scroll").display = False
        if len(self.providers) == 1:
            self._activate_provider(0)
        else:
            self._show_selection(True)

    def _clean(self, text: str) -> str:
        return clean_text(text, self._secrets)

    def on_resize(self, event: events.Resize) -> None:
        self.call_after_refresh(self._reflow_log)

    def _reflow_log(self) -> None:
        """宽度改变后重新排版已完成的内容，避免旧的宽行被裁切。"""
        if not self._transcript or self._quitting:
            return
        self.query_one("#startup-artwork", BannerArtwork).fit_to_window()
        log = self.query_one("#log", RichLog)
        log.clear()
        for block in self._transcript:
            log.write(block)
        log.refresh(layout=True)

    def _write(self, block: RenderableType) -> None:
        self._transcript.append(block)
        self.query_one("#transcript", TranscriptViewport).follow_tail = True
        log = self.query_one("#log", RichLog)
        log.write(block)
        log.refresh(layout=True)

    def _show_selection(self, selecting: bool) -> None:
        for selector in ("#providers", "#selection-title"):
            self.query_one(selector).display = selecting
        for selector in ("#transcript", "#composer", "#statusbar"):
            self.query_one(selector).display = not selecting
        if selecting:
            self.query_one("#providers", OptionList).focus()

    def _activate_provider(self, index: int) -> None:
        self.provider = new_provider(self.providers[index])
        self._provider_closed = False
        self.state = SessionState.IDLE
        self._show_selection(False)
        self._refresh_statusbar()
        self.query_one("#input", ChatInput).focus()

    def _refresh_statusbar(self) -> None:
        if self.provider is not None:
            self.query_one("#statusbar", Static).update(
                status_bar(
                    self.mode,
                    self._clean(self.provider.model),
                    self.usage_in,
                    self.usage_out,
                )
            )

    @on(OptionList.OptionSelected, "#providers")
    def select_provider(self, event: OptionList.OptionSelected) -> None:
        if self.state == SessionState.SELECTING:
            self._activate_provider(event.option_index)

    @on(ChatInput.Submitted)
    async def input_submitted(self, event: ChatInput.Submitted) -> None:
        await self.submit(event.text)

    async def submit(self, text: str) -> None:
        if self.state != SessionState.IDLE or self._quitting or not text.strip():
            return
        if text.strip() == "/exit":
            await self.action_quit()
            return
        editor = self.query_one("#input", ChatInput)
        editor.clear()
        if text.strip() == "/plan":
            self.mode = Mode.PLAN
            self._write(Text("已进入计划模式（只读工具）；/do 开始执行。", style="dim"))
            self._refresh_statusbar()
            return
        if text.strip() == "/do":
            self.mode = Mode.DEFAULT
            self.conv.add_user(EXECUTE_DIRECTIVE)
        else:
            self.conv.add_user(text)
        self._write(user_block(self._clean(text)))
        self._refresh_statusbar()
        self.turn_cancel = asyncio.Event()
        self.iter = 0
        editor.read_only = True
        self.cur_reply = ""
        self.turn_start = time.monotonic()
        self.state = SessionState.STREAMING
        self.query_one("#streaming-scroll").display = True
        self._refresh_streaming_view()
        self._timer = self.set_interval(0.1, lambda: tick(self))
        self._stream_task = asyncio.create_task(consume_stream(self))

    def _refresh_streaming_view(self) -> None:
        if self.state == SessionState.APPROVING and self.pending is not None:
            request = replace(
                self.pending,
                name=self._clean(self.pending.name),
                args=self._clean(self.pending.args),
                reason=self._clean(self.pending.reason),
            )
            self.query_one("#streaming", Static).update(
                approval_block(request, self.approve_cursor)
            )
            return
        if self.state != SessionState.STREAMING:
            return
        elapsed = int(time.monotonic() - self.turn_start)
        spinner = ("⠋", "⠙", "⠹", "⠸")[
            int((time.monotonic() - self.turn_start) * 8) % 4
        ]
        content = Text()
        if self.cur_reply:
            content.append("● " + self._clean(self.cur_reply) + "\n")
        progress = f" · 第 {self.iter} 轮" if self.iter else ""
        if self.cur_tools:
            for tool in self.cur_tools:
                content.append(
                    self._clean(f"● {tool.name}({tool.args})\n"), style="bold cyan"
                )
                content.append(
                    f"{spinner} Running… ({elapsed}s{progress})\n", style="dim"
                )
        else:
            content.append(f"{spinner} Imagining… ({elapsed}s{progress})", style="dim")
        self.query_one("#streaming", Static).update(content)
        self.query_one("#streaming-scroll", VerticalScroll).scroll_end(animate=False)

    def _finish_with_assistant(self, reply: str) -> None:
        self._write(
            render_markdown(self._clean(reply), time.monotonic() - self.turn_start)
        )
        self._return_to_idle()

    def _finish_with_error(self, error: Exception) -> None:
        if self.cur_reply:
            self._write(Text("● " + self._clean(self.cur_reply) + "\n[本轮未完成]"))
        self._write(
            error_block(
                self._clean(error_message(error)), time.monotonic() - self.turn_start
            )
        )
        self._return_to_idle()

    def _return_to_idle(self) -> None:
        if self._timer:
            self._timer.stop()
            self._timer = None
        self._stream_task = None
        self.cur_reply = ""
        self.cur_tools = []
        self.iter = 0
        self.turn_cancel = None
        self.pending = None
        self.state = SessionState.IDLE
        self.query_one("#streaming", Static).update("")
        self.query_one("#streaming-scroll").display = False
        editor = self.query_one("#input", ChatInput)
        editor.read_only = False
        editor.focus()

    def action_history_up(self) -> None:
        viewport = self.query_one("#transcript", TranscriptViewport)
        viewport.follow_tail = False
        viewport.scroll_page_up(animate=False)

    def action_history_down(self) -> None:
        viewport = self.query_one("#transcript", TranscriptViewport)
        viewport.follow_tail = False
        viewport.scroll_page_down(animate=False)

    async def _close_provider(self) -> None:
        if self.provider is not None and not self._provider_closed:
            self._provider_closed = True
            with suppress(Exception):
                await asyncio.wait_for(self.provider.aclose(), timeout=2)

    def action_cancel_turn(self) -> None:
        if (
            self.state in (SessionState.STREAMING, SessionState.APPROVING)
            and self.turn_cancel is not None
        ):
            if self.pending is not None and not self.pending.respond.done():
                self.pending.respond.set_result(Outcome.DENY_ONCE)
            self.turn_cancel.set()

    def action_next_mode(self) -> None:
        if self.state == SessionState.IDLE:
            self.mode = next_mode(self.mode)
            self._write(Text(f"已切换到 {self.mode} 模式", style="dim"))
            self._refresh_statusbar()

    def on_key(self, event: events.Key) -> None:
        if self.state == SessionState.APPROVING:
            event.stop()
            event.prevent_default()
            update_approving(self, event.key)

    async def action_interrupt(self) -> None:
        if self.state in (SessionState.STREAMING, SessionState.APPROVING):
            self.action_cancel_turn()
        else:
            await self.action_quit()

    async def action_quit(self) -> None:
        self._quitting = True
        if self._timer:
            self._timer.stop()
            self._timer = None
        if self._stream_task:
            self._stream_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._stream_task
            self._stream_task = None
        if self.state in (SessionState.STREAMING, SessionState.APPROVING):
            if self.cur_reply:
                self._write(
                    Text("● " + self._clean(self.cur_reply) + "\n[退出时未完成]")
                )
            self.conv.discard_pending_user()
        await self._close_provider()
        self.exit()

    async def on_unmount(self) -> None:
        if self._stream_task:
            self._stream_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._stream_task
        await self._close_provider()

    def print_transcript(self, console: Console | None = None) -> None:
        """退出后把完整静态记录写回普通终端，而非仅留下最后一个可见窗口。"""
        output = console or Console()
        print_banner_image(output)
        for block in self._transcript:
            output.print(block)
