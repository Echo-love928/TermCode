"""使用 Textual Pilot 驱动真实界面与本地 HTTP/SSE 适配器。"""

import asyncio
import io
import re

import pytest
from rich.console import Console
from textual import events
from textual.widgets import OptionList, RichLog, Static

from termcode.config import ProviderConfig
from termcode.tui.app import ChatInput, SessionState, TermCodeApp


def local_config(server, protocol="openai", name="local"):
    return ProviderConfig(
        name=name,
        protocol=protocol,
        api_key="TEST_SECRET",
        model="test-model",
        base_url=server.url + ("/v1" if protocol == "openai" else ""),
        thinking=protocol == "anthropic",
    )


async def wait_for(predicate, timeout=5):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), timeout)


def transcript(app):
    output = io.StringIO()
    app.print_transcript(Console(file=output, width=80, color_system=None))
    return output.getvalue()


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
async def test_single_provider_multiline_multiturn_markdown_and_exit(
    sse_server, protocol, tmp_path
):
    app = TermCodeApp([local_config(sse_server, protocol)])
    async with app.run_test(size=(90, 36)) as pilot:
        assert app.state == SessionState.IDLE
        editor = app.query_one("#input", ChatInput)
        assert editor.has_focus
        assert not app.query_one("#providers").display
        await pilot.press(*list("first"), "alt+enter", *list("second"))
        assert editor.text == "first\nsecond"
        await pilot.press("enter")
        await wait_for(lambda: len(app.conv.messages()) == 2)
        assert app.state == SessionState.IDLE
        assert editor.text == ""
        assert not editor.read_only
        assert app._timer is None
        editor.load_text("remember first message?")
        await pilot.press("enter")
        await wait_for(lambda: len(app.conv.messages()) == 4)
        messages = sse_server.requests[1]["body"]["messages"]
        if protocol == "openai":
            messages = messages[1:]
        assert [m["role"] for m in messages] == ["user", "assistant", "user"]
        assert messages[0]["content"] == "first\nsecond"
        assert "print('TermCode')" in transcript(app)
        assert "完成 ·" in transcript(app)
        assert "HIDDEN_THINKING" not in transcript(app)
        assert "TEST_SECRET" not in transcript(app)
        assert app.query_one("#log", RichLog).lines
        app.save_screenshot("ui.svg", path=str(tmp_path))
        editor.load_text("/exit")
        await pilot.press("enter")
    assert app._provider_closed
    assert "v0.1.0" in transcript(app)
    assert TermCodeApp([local_config(sse_server)]).conv.messages() == []


async def test_multi_provider_arrow_selection(sse_server):
    app = TermCodeApp(
        [
            local_config(sse_server, name="first"),
            local_config(sse_server, "anthropic", "second"),
        ]
    )
    async with app.run_test(size=(80, 32)) as pilot:
        assert app.state == SessionState.SELECTING
        assert app.query_one("#providers", OptionList).has_focus
        assert not app.query_one("#composer").display
        await pilot.press("down", "enter")
        assert app.state == SessionState.IDLE
        assert app.provider.name == "second"
        assert app.query_one("#composer").display
        assert app.query_one("#input", ChatInput).has_focus
        app.query_one("#input", ChatInput).load_text("你好")
        await pilot.press("enter")
        await wait_for(lambda: len(app.conv.messages()) == 2)
        assert sse_server.requests[0]["path"] == "/v1/messages"


async def test_timer_busy_guard_narrow_resize_and_ctrl_c(sse_server):
    sse_server.gate.clear()
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test(size=(80, 32)) as pilot:
        await app.submit("等待回复")
        await wait_for(lambda: bool(sse_server.requests))
        assert app.state == SessionState.STREAMING
        assert app.query_one("#input", ChatInput).read_only
        await app.submit("不应提交")
        assert len(sse_server.requests) == 1
        assert len(app.conv.messages()) == 1
        await pilot.pause(1.1)
        status = str(app.query_one("#streaming", Static).content)
        match = re.search(r"Imagining… \((\d+)s · 第 1 轮\)", status)
        assert match and int(match.group(1)) >= 1
        await pilot.resize_terminal(38, 20)
        await pilot.press("pageup", "pagedown")
        assert app.query_one("#input").region.right <= 38
        assert app.query_one("#composer").region.bottom <= 20
        await pilot.press("ctrl+c")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert not app._quitting
        assert app.conv.last_role() == "assistant"
        assert "已取消" in transcript(app)
        await pilot.press("ctrl+c")
    assert app._quitting
    assert app._stream_task is None
    assert app._provider_closed
    assert app.conv.last_role() == "assistant"


async def test_error_is_redacted_recoverable_and_history_closed(sse_server):
    sse_server.mode = "auth_error"
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test() as pilot:
        await app.submit("触发错误")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert app.conv.last_role() == "assistant"
        assert "认证失败" in transcript(app)
        assert "TEST_SECRET" not in transcript(app)
        assert app._timer is None
        sse_server.mode = "normal"
        app.query_one("#input", ChatInput).load_text("恢复对话")
        await pilot.press("enter")
        await wait_for(lambda: len(app.conv.messages()) == 4)
        assert "完成 ·" in transcript(app)
        assert len(sse_server.requests) == 2
        assert len(sse_server.requests[1]["body"]["messages"]) == 4


async def test_partial_stream_failure_is_visible_and_recoverable(sse_server):
    sse_server.mode = "truncated"
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test():
        await app.submit("触发断流")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert "本轮未完成" in transcript(app)
        assert app.conv.last_role() == "assistant"
        assert app.query_one("#input", ChatInput).read_only is False


async def test_burst_input_is_processed_before_enter(sse_server):
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test():
        for character in "第一行":
            app.post_message(events.Key(character, character))
        app.post_message(events.Key("alt+enter", None))
        for character in "第二行":
            app.post_message(events.Key(character, character))
        app.post_message(events.Key("enter", "\r"))
        await wait_for(lambda: len(app.conv.messages()) == 2)
        assert (
            sse_server.requests[0]["body"]["messages"][-1]["content"]
            == "第一行\n第二行"
        )


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_plan_do_mode_usage_and_multiround_ui(sse_server, protocol, tmp_path):
    import json

    from termcode.agent import Mode
    from termcode.prompt import EXECUTE_DIRECTIVE

    target = tmp_path / "planned.txt"
    source = tmp_path / "source.txt"
    source.write_text("计划依据", encoding="utf-8")

    def tool(name, **args):
        return {"id": "c1", "name": name, "input": json.dumps(args)}

    sse_server.responses = [
        {"calls": [tool("read_file", path=str(source))]},
        {"text": "计划：创建摘要文件。"},
        {"calls": [tool("write_file", path=str(target), content="计划摘要")]},
        {"calls": [tool("read_file", path=str(target))]},
        {"text": "按计划执行完成。"},
    ]
    app = TermCodeApp([local_config(sse_server, protocol)])
    async with app.run_test() as pilot:
        await app.submit("/plan")
        assert app.mode == Mode.PLAN
        assert app.conv.messages() == []
        assert "PLAN" in transcript_status(app)
        await app.submit("读取依据并制定创建摘要文件的计划")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert not target.exists()
        assert len(sse_server.requests) == 2
        first_usage = app.usage_in
        assert first_usage > 0 and app.usage_out == 20
        await app.submit("/do")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert app.mode == Mode.NORMAL
        assert "PLAN" not in transcript_status(app)
        assert target.read_text(encoding="utf-8") == "计划摘要"
        assert app.usage_in > first_usage and app.usage_out == 50
        assert app.conv.messages()[4].content == EXECUTE_DIRECTIVE
        assert "按计划执行完成" in transcript(app)
        assert app.cur_tools == [] and app.turn_cancel is None and app.iter == 0
        assert "tok" in transcript_status(app)
        await pilot.press("ctrl+c")


def transcript_status(app):
    output = io.StringIO()
    Console(file=output, width=100, color_system=None).print(
        app.query_one("#statusbar", Static).content
    )
    return output.getvalue()


@pytest.mark.parametrize("key", ["escape", "ctrl+c"])
async def test_cancel_then_resume_same_conversation(sse_server, key):
    sse_server.gate.clear()
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test() as pilot:
        await app.submit("等待回复")
        await wait_for(lambda: bool(sse_server.requests))
        await pilot.press(key)
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert not app._quitting
        assert app.conv.last_role() == "assistant"
        assert "已取消" in transcript(app)
        sse_server.gate.set()
        await app.submit("继续正常对话")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert len(app.conv.messages()) == 4
        assert len(sse_server.requests[-1]["body"]["messages"]) == 4
