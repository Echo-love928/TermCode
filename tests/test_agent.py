"""验证工具回灌、错误恢复、批量顺序执行和单轮上限。"""

import json

from termcode.agent import Agent, Phase
from termcode.conversation import Conversation
from termcode.llm import StreamEvent, ToolCall
from termcode.tool import new_default_registry


class FakeProvider:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    async def stream(self, msgs, tools=None):
        self.requests.append((msgs, tools))
        for event in self.responses.pop(0):
            yield event


async def test_agent_read_result_history_and_final(tmp_path):
    path = tmp_path / "x.txt"
    path.write_text("真实内容", encoding="utf-8")
    call = ToolCall("c1", "read_file", json.dumps({"path": str(path)}))
    provider = FakeProvider(
        [
            [
                StreamEvent(text="先读取"),
                StreamEvent(tool_calls=[call]),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="文件已读取"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("读文件并总结")
    events = [ev async for ev in Agent(provider, new_default_registry()).run(conv)]
    assert events[-1].done
    assert [e.tool.phase for e in events if e.tool] == [Phase.START, Phase.END]
    history = conv.messages()
    assert [m.role for m in history] == ["user", "assistant", "tool", "assistant"]
    assert history[1].content == "先读取" and history[1].tool_calls == [call]
    assert "真实内容" in history[2].tool_results[0].content
    assert history[-1].content == "文件已读取"
    assert provider.requests[1][0] == history[:3]
    assert len(provider.requests[0][1]) == 6


async def test_agent_batch_order_errors_and_second_round_limit(tmp_path):
    path = tmp_path / "x.txt"

    def call(id, name, **args):
        return ToolCall(id, name, json.dumps(args))

    first = [
        call("1", "write_file", path=str(path), content="one"),
        call("2", "read_file", path=str(path)),
        call("3", "missing"),
    ]
    ignored = call("4", "write_file", path=str(path), content="should not happen")
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=first), StreamEvent(done=True)],
            [StreamEvent(tool_calls=[ignored]), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("依次操作")
    events = [ev async for ev in Agent(provider, new_default_registry()).run(conv)]
    assert sum(e.tool is not None and e.tool.phase == Phase.END for e in events) == 3
    assert path.read_text() == "one"
    assert "one" in conv.messages()[2].tool_results[1].content
    assert conv.messages()[2].tool_results[2].is_error
    assert "上限" in conv.messages()[-1].content
    assert events[-1].done and len(provider.requests) == 2


async def test_plain_text_and_failed_continuation(tmp_path):
    conv = Conversation()
    conv.add_user("hi")
    provider = FakeProvider([[StreamEvent(text="hello"), StreamEvent(done=True)]])
    assert [e async for e in Agent(provider, new_default_registry()).run(conv)][-1].done
    assert conv.messages()[-1].content == "hello"
    provider = FakeProvider(
        [
            [
                StreamEvent(tool_calls=[ToolCall("1", "read_file", "{}")]),
                StreamEvent(done=True),
            ],
            [StreamEvent(err=RuntimeError("断流"))],
        ]
    )
    events = [e async for e in Agent(provider, new_default_registry()).run(conv)]
    assert events[-1].err is not None
    assert conv.messages()[-1].role == "tool"
    provider = FakeProvider([[StreamEvent(text="partial")]])
    assert [e async for e in Agent(provider, new_default_registry()).run(conv)][-1].err
