"""通过真实 SDK、HTTP 和 Textual Pilot 验证审批与模式状态机。"""

import asyncio
from pathlib import Path

import pytest
from test_tool_protocols import call
from test_tui import local_config, transcript, transcript_status, wait_for
from textual.widgets import Static

from termcode.permission import Mode, Outcome, new_engine
from termcode.tui.app import SessionState, TermCodeApp


@pytest.fixture(autouse=True)
def isolated_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))


async def test_cycle_status_and_mode_persists_across_turns(sse_server):
    app = TermCodeApp([local_config(sse_server, name="PROVIDER_SHOULD_NOT_APPEAR")])
    async with app.run_test() as pilot:
        original = app.engine.user
        for mode, label in [
            (Mode.ACCEPT_EDITS, "ACCEPT EDITS"),
            (Mode.PLAN, "PLAN"),
            (Mode.BYPASS, "BYPASS"),
            (Mode.DEFAULT, "DEFAULT"),
        ]:
            await pilot.press("shift+tab")
            assert app.mode == mode and app.state == SessionState.IDLE
            assert label in transcript_status(app)
            assert "PROVIDER_SHOULD_NOT_APPEAR" not in transcript_status(app)
            assert app.engine.user is original
        await pilot.press("shift+tab")
        await app.submit("你好")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert app.mode == Mode.ACCEPT_EDITS
        assert "已切换到" in transcript(app)


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize(
    "keys,outcome",
    [
        (("down", "enter"), Outcome.ALLOW_FOREVER),
        (("up", "enter"), Outcome.DENY_ONCE),
        (("1",), Outcome.ALLOW_ONCE),
        (("2",), Outcome.ALLOW_FOREVER),
        (("3",), Outcome.DENY_ONCE),
        (("space",), Outcome.ALLOW_ONCE),
        (("y",), Outcome.ALLOW_ONCE),
        (("n",), Outcome.DENY_ONCE),
        (("j", "k", "enter"), Outcome.ALLOW_ONCE),
    ],
)
async def test_real_approval_menu_keys(sse_server, protocol, keys, outcome, tmp_path):
    sse_server.responses = [
        {"calls": [call("write_file", path="file.txt", content="written")]},
        {"text": "继续回复"},
    ]
    app = TermCodeApp([local_config(sse_server, protocol)])
    async with app.run_test(size=(90, 40)) as pilot:
        await app.submit("请创建一个文件")
        await wait_for(lambda: app.state == SessionState.APPROVING)
        request = app.pending
        assert app.approve_cursor == 0
        content = str(app.query_one("#streaming", Static).content)
        for text in [
            "write_file",
            "file.txt",
            "需确认",
            "> 1. 允许本次",
            "2. 永久允许",
            "3. 拒绝本次",
        ]:
            assert text in content
        await pilot.press("shift+tab")
        assert app.mode == Mode.DEFAULT
        await pilot.press(*keys)
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert request.respond.result() == outcome
        assert (await asyncio.to_thread((tmp_path / "file.txt").exists)) == (
            outcome != Outcome.DENY_ONCE
        )
        assert (await asyncio.to_thread(Path(app.engine.local_path).exists)) == (
            outcome == Outcome.ALLOW_FOREVER
        )
        assert "继续回复" in transcript(app)
        assert app.pending is None


@pytest.mark.parametrize("key", ["escape", "ctrl+c"])
async def test_approval_cancel_and_followup_no_task_leaks(sse_server, key):
    sse_server.responses = [
        {"calls": [call("write_file", path="never.txt", content="never")]},
        {"text": "取消后继续成功"},
    ]
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test() as pilot:
        await app.submit("写文件")
        await wait_for(lambda: app.state == SessionState.APPROVING)
        request = app.pending
        worker = app._stream_task
        await pilot.press(key)
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert request.respond.result() == Outcome.DENY_ONCE
        assert (
            worker.done()
            and not app._quitting
            and not (await asyncio.to_thread(Path("never.txt").exists))
        )
        assert app.conv.last_role() == "assistant"
        assert len(app.conv.messages()[2].tool_results) == 1
        await app.submit("继续")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert "取消后继续成功" in transcript(app)
        assert not any(not t.done() and t is worker for t in asyncio.all_tasks())


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_cross_protocol_blacklist_sandbox_and_loop_feedback(sse_server, protocol):
    sse_server.responses = [
        {"calls": [call("bash", command="rm -rf /")]},
        {"calls": [call("write_file", path="../outside.txt", content="never")]},
        {"calls": [call("write_file", path="inside.txt", content="safe")]},
        {"text": "调整路径后完成"},
    ]
    app = TermCodeApp([local_config(sse_server, protocol)])
    app.mode = Mode.BYPASS
    async with app.run_test():
        await app.submit("执行任务")
        await wait_for(lambda: app.state == SessionState.IDLE)
        history = app.conv.messages()
        assert "黑名单" in history[2].tool_results[0].content
        assert "路径在项目目录之外" in history[4].tool_results[0].content
        assert not history[6].tool_results[0].is_error
        assert (await asyncio.to_thread(Path("inside.txt").read_text)) == "safe"
        assert not (await asyncio.to_thread(Path("../outside.txt").exists))
        assert len(sse_server.requests) == 4


async def test_config_plan_startup_and_do_requests_approval(sse_server, tmp_path):
    config = tmp_path / ".termcode/settings.yaml"
    config.parent.mkdir()
    config.write_text("default_mode: plan\n")
    engine, err = new_engine(str(tmp_path))
    assert err is None
    app = TermCodeApp([local_config(sse_server)], engine=engine)
    sse_server.responses = [
        {"text": "计划完成"},
        {"calls": [call("write_file", path="planned.txt", content="ok")]},
        {"text": "执行完成"},
    ]
    async with app.run_test() as pilot:
        assert app.mode == Mode.PLAN
        await app.submit("做计划")
        await wait_for(lambda: app.state == SessionState.IDLE)
        first = sse_server.requests[0]["body"]
        assert [t["function"]["name"] for t in first["tools"]] == [
            "read_file",
            "glob",
            "grep",
        ]
        assert any("system-reminder" in str(m) for m in first["messages"])
        await app.submit("/do")
        await wait_for(lambda: app.state == SessionState.APPROVING)
        assert app.mode == Mode.DEFAULT
        await pilot.press("1")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert await asyncio.to_thread(Path("planned.txt").exists)
