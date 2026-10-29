"""启动选择、输入、流式输出和退出组成的终端状态机。"""

import asyncio
import os
import time
from contextlib import suppress
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
from termcode.agent import ToolEvent
from termcode.config import ProviderConfig
from termcode.conversation import Conversation
from termcode.llm import Provider, new_provider
from termcode.prompt import StartupBanner
from termcode.tool import Registry, new_default_registry
from termcode.tui.banner import (
    BannerArtwork,
    TranscriptLog,
    TranscriptViewport,
    graphics_available,
    print_banner_image,
)
from termcode.tui.select import provider_list
from termcode.tui.stream import consume_stream, tick
from termcode.tui.view import (
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


class ChatInput(TextArea):
    class Submitted(UIMessage):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def on_key(self, event: events.Key) -> None:
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
        Binding("ctrl+c", "quit", "退出", priority=True),
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
        self, providers: list[ProviderConfig], registry: Registry | None = None
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
        self._cur_tool: ToolEvent | None = None
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
        self.query_one("#statusbar", Static).update(
            status_bar(
                self._clean(self.provider.name), self._clean(self.provider.model)
            )
        )
        self.query_one("#input", ChatInput).focus()

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
        self.conv.add_user(text)
        self._write(user_block(self._clean(text)))
        editor = self.query_one("#input", ChatInput)
        editor.clear()
        editor.read_only = True
        self.cur_reply = ""
        self.turn_start = time.monotonic()
        self.state = SessionState.STREAMING
        self.query_one("#streaming-scroll").display = True
        self._refresh_streaming_view()
        self._timer = self.set_interval(0.1, lambda: tick(self))
        self._stream_task = asyncio.create_task(consume_stream(self))

    def _refresh_streaming_view(self) -> None:
        if self.state != SessionState.STREAMING:
            return
        elapsed = int(time.monotonic() - self.turn_start)
        spinner = ("⠋", "⠙", "⠹", "⠸")[
            int((time.monotonic() - self.turn_start) * 8) % 4
        ]
        content = Text()
        if self.cur_reply:
            content.append("● " + self._clean(self.cur_reply) + "\n")
        if self._cur_tool is not None:
            content.append(
                self._clean(f"● {self._cur_tool.name}({self._cur_tool.args})\n"),
                style="bold cyan",
            )
            content.append(f"{spinner} Running… ({elapsed}s)", style="dim")
        else:
            content.append(f"{spinner} Imagining… ({elapsed}s)", style="dim")
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
        self.conv.discard_pending_user()
        self._return_to_idle()

    def _return_to_idle(self) -> None:
        if self._timer:
            self._timer.stop()
            self._timer = None
        self._stream_task = None
        self.cur_reply = ""
        self._cur_tool = None
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
        if self.state == SessionState.STREAMING:
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
