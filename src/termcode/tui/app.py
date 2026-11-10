"""启动选择、输入、流式输出和退出组成的终端状态机。"""

import asyncio
import os
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from enum import Enum
from functools import partial
from pathlib import Path

from rich.console import Console, RenderableType
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message as UIMessage
from textual.timer import Timer
from textual.widgets import OptionList, RichLog, Static, TextArea

from termcode import __version__, coordinator
from termcode.agent import Agent, ApprovalRequest, SessionRuntime, ToolEvent
from termcode.agent.agent_tool import AgentTool
from termcode.command import Registry as CommandRegistry
from termcode.command import register_builtins
from termcode.command.skills import register_skill_commands, register_skill_manager
from termcode.config import ProviderConfig, effective_context_window
from termcode.conversation import Conversation
from termcode.hook import Engine as HookEngine
from termcode.hook import Event as HookEvent
from termcode.llm import Provider, new_provider
from termcode.memory import Manager
from termcode.permission import Engine, Mode, Outcome, new_engine
from termcode.prompt import StartupBanner
from termcode.session import Writer
from termcode.skills.executor import SkillExecutor
from termcode.skills.loader import SkillLoader
from termcode.skills.parser import SkillDef
from termcode.subagent import Catalog
from termcode.task import Manager as TaskManager
from termcode.team import Manager as TeamManager
from termcode.tool import Registry, new_default_registry, with_cwd
from termcode.tool.install_skill import InstallSkillTool
from termcode.tool.load_skill import LoadSkill
from termcode.tui.banner import (
    BannerArtwork,
    TranscriptLog,
    TranscriptViewport,
    graphics_available,
    print_banner_image,
)
from termcode.tui.commands import CommandUI, dispatch_slash
from termcode.tui.complete import CompletionMenu
from termcode.tui.select import provider_list
from termcode.tui.stream import (
    begin_autonomous_turn,
    consume_stream,
    tick,
    update_approving,
)
from termcode.tui.tasks import LeadMailMessage, consume_lead_mail
from termcode.tui.view import (
    approval_block,
    clean_text,
    error_block,
    error_message,
    render_markdown,
    status_bar,
    user_block,
)
from termcode.tui.worktree_adapter import WorktreeAdapter
from termcode.worktree import Manager as WorktreeManager


class SessionState(Enum):
    SELECTING = "selecting"
    IDLE = "idle"
    STREAMING = "streaming"
    APPROVING = "approving"
    RESUMING = "resuming"
    SUBMITTING = "submitting"


def next_mode(mode: Mode) -> Mode:
    return Mode((int(mode) + 1) % len(Mode))


class ChatInput(TextArea):
    class Submitted(UIMessage):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    async def on_key(self, event: events.Key) -> None:
        app = self.app
        if isinstance(app, TermCodeApp):
            if app.state == SessionState.APPROVING:
                app.on_key(event)
                return
            if await app._handle_completion_key(event, self):
                return
        if event.key == "shift+tab":
            # 交给应用级模式绑定，不让编辑器将其解释为反向缩进。
            return
        # 与普通字符在同一控件队列中处理，避免优先绑定抢在粘贴文本之前执行。
        if event.key in {"enter", "ctrl+j", "alt+enter"}:
            event.stop()
            event.prevent_default()
            if event.key == "enter":
                self.action_send()
            else:
                self.action_newline()

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
    #completion { height: auto; max-height: 8; margin: 0 1; }
    #submit-error { height: auto; max-height: 4; margin: 0 1; color: $error; }
    #statusbar { height: 1; width: 1fr; }
    """

    def __init__(
        self,
        providers: list[ProviderConfig],
        registry: Registry | None = None,
        engine: Engine | None = None,
        *,
        runtime: SessionRuntime | None = None,
        writer: Writer | None = None,
        mem_mgr: Manager | None = None,
        instruction_text: str = "",
        memory_text: str = "",
        cancel_background: Callable[[], None] | None = None,
        skill_loader: SkillLoader | None = None,
        hook_engine: HookEngine | None = None,
        task_mgr: TaskManager | None = None,
        subagent_catalog: Catalog | None = None,
        enable_subagent_background: bool = True,
        worktree_mgr: WorktreeManager | None = None,
        team_mgr: TeamManager | None = None,
        coordinator_mode: bool = False,
    ) -> None:
        super().__init__()
        if not providers:
            raise ValueError("至少需要一项供应商配置")
        self.providers = providers
        self.provider: Provider | None = None
        self._provider_config: ProviderConfig | None = None
        self.agent: Agent | None = None
        self.runtime = runtime if runtime is not None else SessionRuntime()
        self.hook_engine = (
            hook_engine if hook_engine is not None else self.runtime.hook_engine
        )
        self.runtime.hook_engine = self.hook_engine
        self._hook_session_ended = False
        self.cmd_registry = CommandRegistry()
        register_builtins(self.cmd_registry)
        self.command_ui = CommandUI(self)
        self.completion = CompletionMenu()
        self._completion_text = ""
        self._pending_println: list[str] = []
        self._manual_compact = False
        self.state = SessionState.SELECTING
        self.writer = writer
        self.mem_mgr = mem_mgr
        self._cancel_background = cancel_background
        self.task_mgr = task_mgr
        self.subagent_catalog = subagent_catalog
        self.enable_subagent_background = enable_subagent_background
        self.worktree_mgr = worktree_mgr
        self.team_mgr = team_mgr
        self.coordinator_mode = coordinator_mode
        self.lead_mail_event = asyncio.Event()
        self._lead_mail_worker: asyncio.Task[None] | None = None
        worktree_session = worktree_mgr.current_session() if worktree_mgr else None
        self.active_cwd = worktree_session.worktree_path if worktree_session else ""
        self._task_done_worker: asyncio.Task[None] | None = None
        self._task_approvals: deque[ApprovalRequest] = deque()
        self.instruction_text = instruction_text
        self.memory_text = memory_text
        self.workspace = os.getcwd()
        self.sessions_dir = str(Path(self.workspace) / ".termcode/sessions")
        self.conv = Conversation(
            writer.on_append if writer else None, writer.on_replace if writer else None
        )
        self._tool_registry = (
            registry if registry is not None else new_default_registry()
        )
        self.skill_loader = skill_loader
        self.skill_executor: SkillExecutor | None = None
        self._load_skill_tool: LoadSkill | None = None
        if skill_loader is not None:
            self._load_skill_tool = LoadSkill()
            self._load_skill_tool.set_loader(skill_loader)
            self._tool_registry.register(self._load_skill_tool)
            self._tool_registry.register(
                InstallSkillTool(skill_loader, self._refresh_skills)
            )
            register_skill_manager(
                self.cmd_registry, skill_loader, self._refresh_skills
            )
            self._refresh_skills()
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
            yield ChatInput(id="input", placeholder="Send a message... · Ctrl+J 换行")
        yield Static("", id="completion", markup=False)
        yield Static("", id="submit-error", markup=False)
        yield Static("", id="statusbar", markup=False)

    async def on_mount(self) -> None:
        self._write(
            StartupBanner(
                self._clean(__version__),
                self._clean(os.getcwd()),
                show_name=not graphics_available(),
            )
        )
        self.query_one("#streaming-scroll").display = False
        self.query_one("#completion").display = False
        self.query_one("#submit-error").display = False
        if len(self.providers) == 1:
            self._activate_provider(0)
        else:
            self._show_selection(True)
        if self.task_mgr is not None:
            self.task_mgr.approval_sink = self._queue_task_approval
            self._task_done_worker = asyncio.create_task(
                self._consume_task_done(self.task_mgr)
            )
        if self.team_mgr is not None:
            self._lead_mail_worker = asyncio.create_task(
                consume_lead_mail(self, self.team_mgr)
            )
        await self._open_hook_session(HookEvent.SESSION_START)

    def on_lead_mail_message(self, _: LeadMailMessage) -> None:
        if self._quitting or self.state != SessionState.IDLE:
            return
        self.lead_mail_event.clear()
        if any(r.startswith("<team-update>") for r in self.runtime.pending_reminders):
            begin_autonomous_turn(self)

    async def _consume_task_done(self, manager: TaskManager) -> None:
        done = manager.subscribe_done()
        while True:
            task_id = await done.get()
            if notice := manager.take_notification(task_id):
                self.runtime.append_reminders([notice])

    def _queue_task_approval(self, request: ApprovalRequest) -> None:
        if self.pending is request:
            self.pending = None
            self.state = SessionState.STREAMING
            self._refresh_streaming_view()
        self._task_approvals.append(request)
        request.respond.add_done_callback(lambda _: self._finish_task_approval(request))
        self._show_task_approval()

    def _show_task_approval(self) -> None:
        if self.state != SessionState.IDLE or self._quitting:
            return
        while self._task_approvals:
            request = self._task_approvals.popleft()
            if not request.respond.done():
                self.query_one("#streaming-scroll").display = True
                self._show_approval(request)
                return

    def _show_approval(self, request: ApprovalRequest) -> None:
        self.pending = request
        self.approve_cursor = 0
        self.state = SessionState.APPROVING
        self.completion.hide()
        self._render_completion()
        editor = self.query_one("#input", ChatInput)
        editor.read_only = True
        editor.blur()
        self._refresh_streaming_view()

    def _finish_task_approval(self, request: ApprovalRequest) -> None:
        if self.pending is request and not self._quitting and self.is_running:
            self._return_to_idle()

    async def _clear_tasks(self) -> None:
        if self.task_mgr is not None:
            await self.task_mgr.clear()
        self._task_approvals.clear()

    async def _open_hook_session(self, event: HookEvent) -> None:
        self._hook_session_ended = False
        await self.runtime.dispatch_hook(event, self.workspace, self.mode)

    async def end_hook_session(self) -> None:
        if not self._hook_session_ended:
            self._hook_session_ended = True
            await self.runtime.dispatch_hook(
                HookEvent.SESSION_END, self.workspace, self.mode
            )

    def _clean(self, text: str) -> str:
        return clean_text(text, self._secrets)

    def on_resize(self, event: events.Resize) -> None:
        self.call_after_refresh(self._reflow_log)
        self.call_after_refresh(self._render_completion)

    def _reflow_log(self) -> None:
        """宽度改变后重新排版已完成的内容，避免旧的宽行被裁切。"""
        if not self._transcript or self._quitting:
            return
        screen = self.screen_stack[0]
        screen.query_one("#startup-artwork", BannerArtwork).fit_to_window()
        log = screen.query_one("#log", RichLog)
        log.clear()
        for block in self._transcript:
            log.write(block)
        log.refresh(layout=True)

    def _write(self, block: RenderableType) -> None:
        self._transcript.append(block)
        # 恢复弹窗存在时，本地查询仍写入下方的主对话区。
        screen = self.screen_stack[0]
        screen.query_one("#transcript", TranscriptViewport).follow_tail = True
        log = screen.query_one("#log", RichLog)
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
        self._provider_config = self.providers[index]
        self.provider = new_provider(self._provider_config)
        if self.writer:
            self.writer.model = self.provider.model
        if self.mem_mgr:
            self.mem_mgr.set_provider(self.provider, self.provider.model)
        self.runtime.context_window = effective_context_window(self.providers[index])
        self.agent = None
        self._ensure_agent()
        self._provider_closed = False
        self.state = SessionState.IDLE
        self._show_selection(False)
        self._refresh_statusbar()
        self.query_one("#input", ChatInput).focus()

    def _ensure_agent(self) -> Agent:
        if self.agent is None:
            if self.provider is None:
                raise RuntimeError("尚未选择供应商")
            self._bind_agent(self._create_agent(self.provider, self.runtime))
        assert self.agent is not None
        return self.agent

    def _create_agent(self, provider: Provider, runtime: SessionRuntime) -> Agent:
        agent = Agent(
            provider,
            self._tool_registry,
            __version__,
            self.engine,
            runtime=runtime,
            memory_manager=self.mem_mgr,
            instruction_text=self.instruction_text,
            memory_text=self.memory_text,
            hook_engine=self.hook_engine,
        )
        if self._provider_config is not None:
            agent.provider_name = self._provider_config.name
        if self.team_mgr is not None:
            agent.team_lead_tools = partial(
                self.team_mgr.lead_registry,
                coordinator=self.coordinator_mode,
            )
        if self.coordinator_mode:
            agent.tool_limit = frozenset(coordinator.allowed_tools())
            agent.prompt_suffix = coordinator.system_prompt_suffix()
        return agent

    def _bind_agent(self, agent: Agent) -> None:
        self.agent = agent
        agent_tool = self._tool_registry.get("Agent")
        if isinstance(agent_tool, AgentTool):
            agent_tool.set_parent(agent)
        if self._load_skill_tool is not None:
            self.skill_executor = SkillExecutor(agent, self._provider_config)
            self._load_skill_tool.set_agent(agent, self.skill_executor)
            agent.set_skill_catalog(self._skill_catalog)

    def active_tool_registry(self) -> Registry:
        if self.agent is not None and (
            self.team_mgr is not None or self.coordinator_mode
        ):
            return self.agent.current_registry()
        return self._tool_registry

    def _refresh_skills(self) -> None:
        if self.skill_loader is None:
            return
        items = self.skill_loader.get_catalog()
        self._skill_catalog = (
            "## Available Skills\n\n"
            + "\n".join(f"- {name}: {' '.join(desc.split())}" for name, desc in items)
            + "\n\nIf the user's request matches a Skill, "
            "call LoadSkill to activate it."
            if items
            else ""
        )
        register_skill_commands(self.cmd_registry, self.skill_loader)
        if self.agent is not None:
            self.agent.set_skill_catalog(self._skill_catalog)

    def _refresh_statusbar(self) -> None:
        if self.provider is not None:
            self.query_one("#statusbar", Static).update(
                status_bar(
                    self.mode,
                    self._clean(self.provider.model),
                    self.usage_in,
                    self.usage_out,
                    coordinator_mode=self.coordinator_mode,
                    team_name=self._clean(self.team_mgr.active_team)
                    if self.team_mgr
                    else "",
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
        if self._quitting or not text.strip():
            return
        if self.state == SessionState.SUBMITTING:
            return
        if text.strip().startswith("/"):
            if self.state != SessionState.RESUMING:
                self.query_one("#input", ChatInput).clear()
            self.completion.hide()
            self._render_completion()
            await self.dispatch_slash(text)
            return
        if self.state != SessionState.IDLE:
            return
        editor = self.query_one("#input", ChatInput)
        if self.hook_engine is not None:
            self.state = SessionState.SUBMITTING
            editor.read_only = True
            try:
                result = await self.runtime.dispatch_hook(
                    HookEvent.USER_PROMPT_SUBMIT,
                    self.active_cwd or self.workspace,
                    self.mode,
                    {"prompt": text},
                )
            finally:
                editor.read_only = False
                self.state = SessionState.IDLE
            if self._quitting:
                return
            if result.blocked:
                error = self.query_one("#submit-error", Static)
                error.update(self._clean(result.block_message()))
                error.display = True
                editor.focus()
                return
        editor.clear()
        self.completion.hide()
        self._render_completion()
        self.conv.add_user(text)
        self._begin_turn(text)

    async def dispatch_slash(self, text: str) -> bool:
        handled = await dispatch_slash(self, text)
        if self.team_mgr is not None and not self._quitting:
            self._refresh_statusbar()
        return handled

    def worktree_accessor(self) -> WorktreeAdapter | None:
        if self.worktree_mgr is None:
            return None
        return WorktreeAdapter(self.worktree_mgr, self._set_active_cwd)

    def _set_active_cwd(self, path: str) -> None:
        self.active_cwd = path

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if (
            event.text_area.id == "input"
            and event.text_area.is_mounted
            and not self._quitting
            and self.state != SessionState.RESUMING
        ):
            feedback = self.query("#submit-error")
            if feedback:
                feedback.first().display = False
            self._sync_completion_from_input(event.text_area.text)

    def _sync_completion_from_input(self, text: str) -> None:
        if text != self._completion_text:
            self._completion_text = text
            if self.state == SessionState.IDLE:
                self.completion.update(text, self.cmd_registry)
            else:
                self.completion.hide()
        self._render_completion()

    def _render_completion(self) -> None:
        if not self.is_running or self._quitting:
            return
        widgets = self.query("#completion")
        if not widgets:
            return
        widget = widgets.first(Static)
        widget.display = self.completion.active and self.state == SessionState.IDLE
        # 矮终端保留输入框、状态栏和至少两行会话区。
        self.completion.set_rows(self.size.height - 8)
        widget.update(self.completion.render(max(1, self.size.width - 2)))

    async def _handle_completion_key(
        self, event: events.Key, editor: ChatInput
    ) -> bool:
        if self.state != SessionState.IDLE or event.key not in {
            "up",
            "down",
            "escape",
            "enter",
            "tab",
        }:
            return False
        # 与编辑器同一队列处理，突发键入后立即回车也使用最新输入。
        self._sync_completion_from_input(editor.text)
        if not self.completion.active:
            return False
        event.stop()
        event.prevent_default()
        if event.key == "up":
            self.completion.move_up()
        elif event.key == "down":
            self.completion.move_down()
        elif event.key == "escape":
            self.completion.hide()
        else:
            selected = self.completion.selected()
            self.completion.hide()
            if selected is not None:
                await self.submit("/" + selected.name)
            elif event.key == "enter":
                await self.submit(editor.text)
        self._render_completion()
        return True

    def _begin_turn(
        self, text: str, *, fork: tuple[SkillDef, str] | None = None
    ) -> None:
        editor = self.query_one("#input", ChatInput)
        self._write(user_block(self._clean(text)))
        self._refresh_statusbar()
        self.turn_cancel = asyncio.Event()
        self.iter = 0
        # 运行中允许编辑草稿和使用本地查询，submit 仍阻止并发对话。
        editor.read_only = False
        self.cur_reply = ""
        self.turn_start = time.monotonic()
        self.state = SessionState.STREAMING
        self.query_one("#streaming-scroll").display = True
        self._refresh_streaming_view()
        self._timer = self.set_interval(0.1, lambda: tick(self))
        with with_cwd(self.active_cwd):
            self._stream_task = asyncio.create_task(consume_stream(self, fork=fork))

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
        self._completion_text = ""
        self._show_task_approval()
        if self.state == SessionState.IDLE:
            self._refresh_statusbar()
            editor.focus()
            self._sync_completion_from_input(editor.text)
            if self.lead_mail_event.is_set():
                self.post_message(LeadMailMessage())

    def action_history_up(self) -> None:
        viewport = self.query_one("#transcript", TranscriptViewport)
        viewport.follow_tail = False
        viewport.scroll_page_up(animate=False)

    def action_history_down(self) -> None:
        viewport = self.query_one("#transcript", TranscriptViewport)
        viewport.follow_tail = False
        viewport.scroll_page_down(animate=False)

    async def _close_provider(self) -> None:
        await self._clear_tasks()
        if self.mem_mgr:
            await self.mem_mgr.close()
        if self.writer:
            self.writer.close()
        if self.provider is not None and not self._provider_closed:
            self._provider_closed = True
            with suppress(Exception):
                await asyncio.wait_for(self.provider.aclose(), timeout=2)

    def action_cancel_turn(self) -> None:
        if self.completion.active:
            self.completion.hide()
            self._render_completion()
            self.query_one("#input", ChatInput).focus()
            return
        if self.state == SessionState.RESUMING:
            from termcode.tui.resume import ResumeScreen

            if isinstance(self.screen, ResumeScreen):
                self.screen.dismiss(None)
            return
        if (
            self.enable_subagent_background
            and self.task_mgr is not None
            and self.task_mgr.request_detach()
        ):
            return
        self._cancel_current_turn()

    def _cancel_current_turn(self) -> None:
        if self.pending is not None and self.pending.background:
            if not self.pending.respond.done():
                self.pending.respond.set_result(Outcome.DENY_ONCE)
            return
        if (
            self.state in (SessionState.STREAMING, SessionState.APPROVING)
            and self.turn_cancel is not None
        ):
            if self.pending is not None and not self.pending.respond.done():
                self.pending.respond.set_result(Outcome.DENY_ONCE)
            self.turn_cancel.set()
            if self._manual_compact and self._stream_task is not None:
                self._stream_task.cancel()

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
            self._cancel_current_turn()
        else:
            await self.action_quit()

    async def action_quit(self) -> None:
        self._quitting = True
        if self._cancel_background:
            self._cancel_background()
        if self.mem_mgr:
            self.mem_mgr.cancel_pending()
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
        await self.end_hook_session()
        await self._close_provider()
        self.exit()

    async def on_unmount(self) -> None:
        self._quitting = True
        if self._stream_task:
            self._stream_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._stream_task
        if self._task_done_worker is not None:
            self._task_done_worker.cancel()
            await asyncio.gather(self._task_done_worker, return_exceptions=True)
        if self._lead_mail_worker is not None:
            self._lead_mail_worker.cancel()
            await asyncio.gather(self._lead_mail_worker, return_exceptions=True)
        if self.task_mgr is not None:
            self.task_mgr.approval_sink = None
        await self.end_hook_session()
        await self._close_provider()

    def print_transcript(self, console: Console | None = None) -> None:
        """退出后把完整静态记录写回普通终端，而非仅留下最后一个可见窗口。"""
        output = console or Console()
        print_banner_image(output)
        for block in self._transcript:
            output.print(block)
