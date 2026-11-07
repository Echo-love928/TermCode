"""通过真实 Textual 按键验证分发、持久化及补全交互。"""

import asyncio
import json
from pathlib import Path

import pytest
from test_tui import local_config, transcript, wait_for
from textual import events
from textual.widgets import RichLog, Static

from termcode.agent import SessionRuntime
from termcode.command import Command, Kind
from termcode.compact import new_session_context
from termcode.llm import Message
from termcode.memory import Manager
from termcode.permission import Mode
from termcode.prompt import EXECUTE_DIRECTIVE
from termcode.session import Writer, load_session
from termcode.tui.app import ChatInput, SessionState, TermCodeApp
from termcode.tui.resume import ResumeScreen


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


async def test_tui_dispatch_help_lists_all_builtins(sse_server):
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test():
        assert await app.dispatch_slash("/help")
        rows = app._transcript[-1].plain.splitlines()
        assert [row.split()[0] for row in rows] == [
            "/" + c.name for c in app.cmd_registry.visible()
        ]
        assert len(rows) == 12
        assert app.conv.length() == 0 and not sse_server.requests
        assert not await app.dispatch_slash("ordinary text")


async def test_tui_dispatch_case_insensitive(sse_server):
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test():
        await app.submit("/Help")
        first = app._transcript[-1].plain
        await app.submit("/help")
        assert app._transcript[-1].plain == first


@pytest.mark.parametrize("text", ["/foobar", "/help xx", "/ /help", "/"])
async def test_tui_unknown_slash_command_friendly(sse_server, text):
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test():
        await app.submit(text)
        assert "未知命令" in transcript(app) and "/help" in transcript(app)
        assert not app.conv.messages() and not sse_server.requests


async def test_tui_dispatch_plan_local_only(sse_server):
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test():
        await app.submit("/plan")
        assert app.mode == Mode.PLAN
        assert not app.conv.messages() and not sse_server.requests


@pytest.mark.parametrize("name", ["do", "review"])
async def test_tui_dispatch_do_injects_and_sends(sse_server, tmp_path, name):
    context = new_session_context(str(tmp_path))
    writer = Writer(context.session_dir)
    app = TermCodeApp(
        [local_config(sse_server)],
        runtime=SessionRuntime(session=context),
        writer=writer,
    )
    async with app.run_test():
        await app.submit("/plan")
        await app.submit("/" + name)
        assert app.state == SessionState.STREAMING
        await wait_for(lambda: app.state == SessionState.IDLE)
        messages = app.conv.messages()
        assert messages[0].role == "user"
        if name == "do":
            assert app.mode == Mode.DEFAULT
            assert messages[0].content == EXECUTE_DIRECTIVE
        else:
            assert app.mode == Mode.PLAN
            assert "审查" in messages[0].content
        stored = load_session(context.session_dir)
        assert stored == messages
        sent = sse_server.requests[0]["body"]["messages"]
        assert messages[0].content in [
            m["content"] for m in sent if m["role"] == "user"
        ]
        assert writer.model == "test-model"


@pytest.mark.parametrize(
    "state", [SessionState.STREAMING, SessionState.APPROVING, SessionState.RESUMING]
)
async def test_busy_local_queries_work_but_mutations_block(sse_server, state):
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test():
        app.state = state
        for name in ("help", "status", "memory", "permission", "session"):
            await app.dispatch_slash("/" + name)
            assert app.state == state
        for name in ("clear", "compact", "do", "exit", "plan", "resume", "review"):
            await app.dispatch_slash("/" + name)
            assert "请等待当前任务完成" in app._transcript[-1].plain
            assert app.state == state and not app._quitting
        assert not app.conv.messages() and not sse_server.requests
        app.state = SessionState.IDLE


async def test_local_query_works_during_actual_stream(sse_server):
    sse_server.gate.clear()
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test():
        await app.submit("真实对话")
        await wait_for(lambda: bool(sse_server.requests))
        await app.submit("/status")
        assert "Mode:" in app._transcript[-1].plain
        assert app.state == SessionState.STREAMING
        assert app.conv.length() == 1
        sse_server.gate.set()
        await wait_for(lambda: app.state == SessionState.IDLE)


async def test_handler_errors_are_redacted_and_hidden_alias_dispatches(sse_server):
    async def fail(ui):
        raise RuntimeError("TEST_SECRET 原因")

    app = TermCodeApp([local_config(sse_server)])
    app.cmd_registry.register(
        Command("hidden", "隐藏", Kind.LOCAL, fail, aliases=["alias"], hidden=True)
    )
    async with app.run_test():
        await app.submit("/ALIAS")
        assert "错误：" in app._transcript[-1].plain
        assert "原因" in app._transcript[-1].plain
        assert "TEST_SECRET" not in transcript(app)
        await app.submit("/help")
        assert "hidden" not in app._transcript[-1].plain


async def test_clear_resets_runtime_and_rebinds_archive(sse_server, tmp_path):
    context = new_session_context(str(tmp_path))
    writer = Writer(context.session_dir)
    runtime = SessionRuntime(session=context)
    app = TermCodeApp([local_config(sse_server)], runtime=runtime, writer=writer)
    async with app.run_test():
        await app.submit("旧会话")
        await wait_for(lambda: app.state == SessionState.IDLE)
        previous = load_session(context.session_dir)
        app.runtime.auto_tracking.record_failure()
        await app.submit("/clear")
        new_context = app.runtime.session
        assert new_context != context
        assert app.conv.messages() == []
        assert app.usage_in == app.usage_out == app.iter == app.runtime.turn_count == 0
        assert app.runtime.usage_anchor == app.runtime.anchor_msg_len == 0
        assert app.runtime.auto_tracking.consecutive_failures == 0
        assert app.agent.runtime is runtime
        assert writer._file.closed
        assert len(app._transcript) == 1
        assert "已清空" in transcript(app)
        assert "旧会话" not in transcript(app)
        assert app.query_one("#log", RichLog).lines
        await app.submit("新会话")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert load_session(context.session_dir) == previous
        stored = load_session(new_context.session_dir)
        assert stored[0] == Message("user", "新会话")
        text = await asyncio.to_thread(
            Path(app.writer.path).read_text, encoding="utf-8"
        )
        rows = text.splitlines()
        assert json.loads(rows[0])["model"] == "test-model"
        await app.submit("/resume")
        await wait_for(lambda: isinstance(app.screen, ResumeScreen))
        assert context.session_id in [i.id for i in app.screen.items]
        await app.dispatch_slash("/status")
        assert "Mode:" in app._transcript[-1].plain
        assert isinstance(app.screen, ResumeScreen)


async def test_clear_failure_preserves_current_session(
    sse_server, tmp_path, monkeypatch
):
    context = new_session_context(str(tmp_path))
    writer = Writer(context.session_dir)
    app = TermCodeApp(
        [local_config(sse_server)],
        writer=writer,
        runtime=SessionRuntime(session=context),
    )

    def fail(*args):
        raise OSError("磁盘不可写")

    async with app.run_test():
        app.conv.add_user("保留历史")
        original = app.conv
        monkeypatch.setattr("termcode.tui.commands.Writer", fail)
        await app.submit("/clear")
        assert app.conv is original and app.writer is writer
        assert not writer._file.closed and app.runtime.session is context
        assert "磁盘不可写" in transcript(app)
        assert "已清空" not in transcript(app)


async def test_completion_keys_scroll_escape_backspace_tab_and_enter(sse_server):
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test(size=(90, 36)) as pilot:
        editor = app.query_one("#input", ChatInput)
        await pilot.press("/")
        assert app.completion.active and len(app.completion.items) == 12
        widget = app.query_one("#completion", Static)
        assert 0 < widget.region.height <= 8
        assert editor.region.bottom < widget.region.bottom
        assert widget.region.bottom <= app.query_one("#statusbar").region.y
        await pilot.press(*(["down"] * 9))
        assert app.completion.offset > 0
        await pilot.press("escape")
        assert editor.text == "/" and not app.completion.active
        await pilot.press("s")
        assert [c.name for c in app.completion.items] == ["session", "status"]
        await pilot.press("down", "up", "tab")
        assert "Session:" in app._transcript[-1].plain
        assert editor.text == "" and not app.completion.active
        await pilot.press("/", "s", "down", "enter")
        assert "Mode:" in app._transcript[-1].plain
        assert editor.text == "" and not app.completion.active
        await pilot.press("/", "s", "escape")
        assert editor.text == "/s" and not widget.display
        await pilot.press("backspace", "backspace")
        assert not app.completion.active and editor.text == ""
        assert not app.conv.messages() and not sse_server.requests
        await pilot.resize_terminal(38, 10)
        await pilot.press("/")
        assert app.completion.active and widget.region.height <= 2
        assert app.query_one("#statusbar").region.bottom <= 10


async def test_completion_zero_match_and_multiline(sse_server):
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test() as pilot:
        editor = app.query_one("#input", ChatInput)
        await pilot.press(*"/zzzz", "tab")
        assert editor.text == "/zzzz" and not app.completion.active
        editor.clear()
        await pilot.press(*"/zzzz", "enter")
        assert editor.text == "" and "未知命令" in app._transcript[-1].plain
        editor.load_text("/help\n/review")
        await pilot.pause()
        assert not app.completion.active
        await pilot.press("enter")
        assert "未知命令" in app._transcript[-1].plain
        assert not app.conv.messages() and not sse_server.requests


async def test_empty_submit_is_noop_and_burst_completion_executes_latest(sse_server):
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test() as pilot:
        count = len(app._transcript)
        await app.submit("")
        await app.submit("   ")
        assert len(app._transcript) == count
        for character in "/status":
            app.post_message(events.Key(character, character))
        app.post_message(events.Key("enter", "\r"))
        await wait_for(lambda: len(app._transcript) > count)
        assert "Mode:" in app._transcript[-1].plain
        assert not app.conv.messages() and not sse_server.requests
        await pilot.press("enter")


async def test_exit_cancels_memory_tasks_before_closing(sse_server, tmp_path):
    manager = Manager(str(tmp_path / "p"), str(tmp_path / "u"))
    cancelled = asyncio.Event()

    async def background():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    task = asyncio.create_task(background())
    manager._tasks.add(task)
    app = TermCodeApp([local_config(sse_server)], mem_mgr=manager)
    async with app.run_test():
        await app.submit("/exit")
        assert cancelled.is_set() and task.cancelled()
        assert app._provider_closed and app._quitting
