"""通过真实 Textual 界面验证压缩命令、状态提示与取消。"""

import asyncio

import pytest
from test_tui import local_config, transcript, wait_for
from textual.widgets import OptionList

from termcode.agent import CompactEvent, CompactPhase, Event
from termcode.permission import Mode
from termcode.prompt import EXECUTE_DIRECTIVE
from termcode.tui.app import SessionState, TermCodeApp
from termcode.tui.commands import (
    BUILTIN_COMMANDS,
    dispatch_command,
    format_compact_notice,
)


class FakeAgent:
    def __init__(self, events=(), failure=None):
        self.events = list(events)
        self.failure = failure
        self.force_calls = 0
        self.run_calls = 0
        self.gate = asyncio.Event()
        self.gate.set()

    async def run_force_compact(self, conv, defs):
        self.force_calls += 1
        await self.gate.wait()
        if self.failure is not None:
            raise self.failure
        return 1000, 500

    async def run(self, *args):
        self.run_calls += 1
        for event in self.events:
            yield event
        yield Event(done=True)


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


async def test_compact_routes_without_ordinary_run_or_user_history(sse_server):
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test():
        fake = FakeAgent()
        app.agent = fake
        app.conv.add_user("原始需求")
        original = app.conv.messages()
        await app.submit("/compact")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert fake.force_calls == 1 and fake.run_calls == 0
        assert app.conv.messages() == original
        assert "已压缩，token 从 1000 降至 500" in transcript(app)
        assert not sse_server.requests


async def test_unknown_and_plan_commands_do_not_enter_history(sse_server):
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test():
        fake = FakeAgent()
        app.agent = fake
        await app.submit("/unknown")
        assert "未知命令" in transcript(app)
        assert all(command in transcript(app) for command in BUILTIN_COMMANDS)
        await app.submit("/plan")
        assert app.mode == Mode.PLAN
        assert "已进入计划模式" in transcript(app)
        assert app.conv.messages() == []
        assert not fake.run_calls and not sse_server.requests


async def test_do_still_starts_run_with_execute_directive(sse_server):
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test():
        fake = FakeAgent()
        app.agent = fake
        await app.submit("/plan")
        await app.submit("/do")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert app.mode == Mode.DEFAULT
        assert app.conv.messages()[0].content == EXECUTE_DIRECTIVE
        assert fake.run_calls == 1 and not fake.force_calls


async def test_exit_still_closes_provider(sse_server):
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test():
        await app.submit("/exit")
        assert app._quitting and app._provider_closed


async def test_compact_failure_is_visible_without_exiting(sse_server):
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test():
        app.agent = FakeAgent(failure=RuntimeError("摘要服务不可用"))
        await app.submit("/compact")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert "压缩失败：摘要服务不可用" in transcript(app)
        assert not app._quitting


async def test_manual_compact_busy_cancel_and_reuse(sse_server):
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test():
        fake = FakeAgent()
        fake.gate.clear()
        app.agent = fake
        await app.submit("/compact")
        await wait_for(lambda: fake.force_calls == 1)
        await app.submit("不应并发发送")
        assert app.state == SessionState.STREAMING
        app.action_cancel_turn()
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert "已取消" in transcript(app)
        assert not fake.run_calls and not app.conv.messages()
        fake.gate.set()
        await app.submit("/compact")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert fake.force_calls == 2


@pytest.mark.parametrize(
    "event,expected",
    [
        (CompactEvent(CompactPhase.BEFORE_AUTO), "正在压缩上下文..."),
        (CompactEvent(CompactPhase.BEFORE_EMERGENCY), "上下文撞墙，自动压缩中..."),
        (
            CompactEvent(CompactPhase.AFTER_AUTO, 167000, 12000),
            "已压缩，token 从 167000 降至 12000",
        ),
        (
            CompactEvent(CompactPhase.AFTER_EMERGENCY, err=RuntimeError("full")),
            "压缩失败：full",
        ),
    ],
)
async def test_tui_renders_compact_events(sse_server, event, expected):
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test():
        app.agent = FakeAgent([Event(compact=event), Event(text="回答")])
        await app.submit("你好")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert expected in transcript(app)
        assert app.conv.messages()[0].content == "你好"
        assert len(app.conv.messages()) == 1
        assert format_compact_notice(event) == expected


async def test_multi_provider_selection_sets_window_and_persists_runtime(sse_server):
    from dataclasses import replace

    app = TermCodeApp(
        [
            local_config(sse_server),
            replace(
                local_config(sse_server, "anthropic", "second"), context_window=80000
            ),
        ]
    )
    runtime = app.runtime
    async with app.run_test() as pilot:
        app.query_one("#providers", OptionList).highlighted = 1
        await pilot.press("enter")
        assert app.runtime is runtime
        assert app.runtime.context_window == 80000
        assert app.agent.runtime is runtime
        agent = app.agent
        await app.submit("你好")
        await wait_for(lambda: app.state == SessionState.IDLE)
        await app.submit("继续")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert app.agent is agent


def test_dispatch_registry():
    assert len(BUILTIN_COMMANDS) == 4
    assert dispatch_command("普通输入") == (None, False)
    assert dispatch_command("/unknown")[1]


@pytest.mark.parametrize("emergency", [False, True])
async def test_compact_notice_visible_before_summary_response(sse_server, emergency):
    app = TermCodeApp([local_config(sse_server)])
    summary = {"text": "<summary>摘要完成</summary>", "delay": 0.4}
    scripts = [summary, {"text": "继续工作"}]
    if emergency:
        scripts.insert(
            0,
            {
                "status": 400,
                "body": {
                    "error": {
                        "message": "too long",
                        "code": "context_length_exceeded",
                    }
                },
            },
        )
    sse_server.responses = scripts
    async with app.run_test():
        if not emergency:
            app.runtime.usage_anchor = 180000
        await app.submit("请继续")
        expected_requests = 2 if emergency else 1
        await wait_for(lambda: len(sse_server.requests) >= expected_requests)
        notice = "上下文撞墙，自动压缩中..." if emergency else "正在压缩上下文..."
        assert notice in transcript(app)
        assert app.state == SessionState.STREAMING
        assert "已压缩，token 从" not in transcript(app)
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert "已压缩，token 从" in transcript(app)
        assert "继续工作" in transcript(app)
