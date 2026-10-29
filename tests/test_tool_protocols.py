"""通过真实 SDK 和本地 HTTP 验证两协议的工具分片、回灌与界面。"""

import asyncio
import json

import pytest
from rich.text import Text
from test_providers import config
from test_tui import local_config, transcript, wait_for
from textual.widgets import Static

from termcode.agent import Agent
from termcode.conversation import Conversation
from termcode.llm import new_provider
from termcode.tool import new_default_registry
from termcode.tui.app import SessionState, TermCodeApp
from termcode.tui.view import tool_result_summary


def call(name, id="c1", **args):
    return {"id": id, "name": name, "input": json.dumps(args, ensure_ascii=False)}


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_protocol_fragmented_batch_and_result_feedback(
    sse_server, protocol, tmp_path
):
    path = str(tmp_path / "x.txt")
    calls = [
        call("write_file", path=path, content="协议测试"),
        call("read_file", id="c2", path=path),
        call("read_file", id="c3", path=path + "missing"),
    ]
    sse_server.responses = [
        {"text": "先执行工具", "calls": calls},
        {"text": "已读取协议测试，第三个文件不存在。"},
    ]
    provider = new_provider(config(sse_server, protocol, thinking=True))
    conv = Conversation()
    conv.add_user("写入并读取文件")
    try:
        events = [e async for e in Agent(provider, new_default_registry()).run(conv)]
    finally:
        await provider.aclose()
    assert not any(e.err for e in events)
    assert events[-1].done
    assert [c.name for c in conv.messages()[1].tool_calls] == [c["name"] for c in calls]
    assert [json.loads(c.input) for c in conv.messages()[1].tool_calls] == [
        json.loads(c["input"]) for c in calls
    ]
    assert "协议测试" in conv.messages()[2].tool_results[1].content
    assert conv.messages()[2].tool_results[2].is_error
    first, second = [r["body"] for r in sse_server.requests]
    assert len(first["tools"]) == len(second["tools"]) == 6
    history = second["messages"]
    if protocol == "anthropic":
        assert "thinking" in first and "thinking" not in second
        assert history[-2]["content"][0] == {"type": "text", "text": "先执行工具"}
        assert history[-2]["content"][1]["input"] == json.loads(calls[0]["input"])
        assert [b["tool_use_id"] for b in history[-1]["content"]] == ["c1", "c2", "c3"]
        assert history[-1]["content"][-1]["is_error"]
    else:
        assert (
            history[-4]["tool_calls"][0]["function"]["arguments"] == calls[0]["input"]
        )
        assert [m["tool_call_id"] for m in history[-3:]] == ["c1", "c2", "c3"]


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_tui_tools_error_recovery_and_transcript(sse_server, protocol, tmp_path):
    path = tmp_path / "x.txt"
    path.write_text("old\n" * 12, encoding="utf-8")
    sse_server.responses = [
        {"text": "先读文件", "calls": [call("read_file", path=str(path))]},
        {"text": "文件中包含 old。"},
        {
            "calls": [
                call("edit_file", path=str(path), old_string="absent", new_string="x")
            ]
        },
        {"text": "修改失败，未找到匹配。"},
        {"text": "可以继续。"},
    ]
    app = TermCodeApp([local_config(sse_server, protocol)])
    async with app.run_test(size=(90, 36)) as pilot:
        await app.submit("读文件并总结")
        await wait_for(lambda: app.state == SessionState.IDLE)
        text = transcript(app)
        assert text.index("先读文件") < text.index("● read_file(") < text.index("⎿")
        assert text.index("⎿") < text.index("文件中包含 old")
        assert "[truncated]" in text
        await app.submit("修改不存在的文本")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert "匹配到 0 处" in transcript(app)
        assert app._cur_tool is None
        await app.submit("继续")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert "可以继续" in transcript(app)
        assert len(app.conv.messages()) == 10
        await pilot.press("pageup", "pagedown")


async def test_tui_running_responsive_and_secrets_redacted(sse_server):
    from termcode.tool import Registry, Result

    entered = asyncio.Event()
    release = asyncio.Event()

    class SlowTool:
        def name(self):
            return "slow"

        def description(self):
            return "测试进行中指示"

        def parameters(self):
            return {"type": "object", "properties": {}}

        async def execute(self, args):
            entered.set()
            await release.wait()
            return Result("TEST_SECRET\n成功")

    registry = Registry()
    registry.register(SlowTool())
    sse_server.responses = [
        {"calls": [call("slow", value="TEST_SECRET")]},
        {"text": "完成"},
    ]
    app = TermCodeApp([local_config(sse_server)], registry=registry)
    async with app.run_test() as pilot:
        await app.submit("慢工具")
        await asyncio.wait_for(entered.wait(), 5)
        await pilot.pause(0.2)
        status = str(app.query_one("#streaming", Static).content)
        assert "Running…" in status and "slow(" in status
        assert "TEST_SECRET" not in status
        await pilot.press("pageup")
        await pilot.resize_terminal(40, 20)
        assert app._cur_tool is not None
        release.set()
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert "TEST_SECRET" not in transcript(app)
        assert "已隐藏密钥" in transcript(app)


def test_error_summary_is_visibly_red():
    summary = tool_result_summary("bad", is_error=True)
    assert isinstance(summary.renderable, Text)
    assert summary.renderable.style == "red"
