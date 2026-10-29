"""验证多轮循环、停止条件、保序并发与取消后的历史配对。"""

import asyncio
import json
from contextlib import aclosing

import pytest

from termcode.agent import (
    EMPTY_REPLY,
    MAX_ITERATIONS,
    MAX_UNKNOWN_RUN,
    NOTICE_CANCELLED,
    NOTICE_MAX_ITER,
    NOTICE_STREAM_ERR,
    NOTICE_UNKNOWN_TOOLS,
    Agent,
    Mode,
    Phase,
)
from termcode.conversation import Conversation
from termcode.llm import StreamEvent, ToolCall, Usage
from termcode.prompt import Environment, build_system_prompt, plan_reminder
from termcode.tool import Registry, Result, new_default_registry


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    # Agent 单测不依赖真实 git 耗时；实际采集与降级由 prompt/协议集成测试覆盖。
    monkeypatch.setattr(
        "termcode.prompt.gather_environment",
        lambda version, model: Environment(
            "/test", "test", "2026-10-29", "", version, model
        ),
    )


class FakeProvider:
    model = "test-model"

    def __init__(self, responses, repeat=False):
        self.responses = responses
        self.requests = []
        self.repeat = repeat

    async def stream(self, req):
        self.requests.append(req)
        response = self.responses[0] if self.repeat else self.responses.pop(0)
        for event in response:
            yield event


def call(id, name, **args):
    return ToolCall(id, name, json.dumps(args, ensure_ascii=False))


def response(*calls, text=""):
    return [
        StreamEvent(text=text, tool_calls=list(calls)),
        StreamEvent(usage=Usage(10, 5)),
        StreamEvent(done=True),
    ]


def conversation():
    conv = Conversation()
    conv.add_user("执行任务")
    return conv


def assert_paired(conv):
    messages = conv.messages()
    for i, message in enumerate(messages):
        if message.tool_calls:
            assert messages[i + 1].role == "tool"
            assert [r.tool_call_id for r in messages[i + 1].tool_results] == [
                c.id for c in message.tool_calls
            ]
    assert conv.last_role() == "assistant"


async def test_agent_multiround_read_write_history_and_final(tmp_path):
    path = tmp_path / "x.txt"
    path.write_text("真实内容", encoding="utf-8")
    target = tmp_path / "summary.txt"
    provider = FakeProvider(
        [
            response(call("1", "read_file", path=str(path)), text="先读取"),
            response(call("2", "write_file", path=str(target), content="摘要")),
            response(text="摘要已写入"),
        ]
    )
    conv = conversation()
    events = [e async for e in Agent(provider, new_default_registry()).run(conv)]
    assert events[-1].done
    assert [e.iter for e in events if e.iter] == [1, 2, 3]
    assert [e.tool.phase for e in events if e.tool] == [
        Phase.START,
        Phase.END,
        Phase.START,
        Phase.END,
    ]
    assert sum(e.usage.input for e in events if e.usage) == 30
    assert sum(e.usage.output for e in events if e.usage) == 15
    history = conv.messages()
    assert [m.role for m in history] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
    ]
    assert "真实内容" in history[2].tool_results[0].content
    assert target.read_text(encoding="utf-8") == "摘要"
    assert json.loads(history[3].tool_calls[0].input)["content"] == "摘要"
    assert provider.requests[2].messages == history[:5]
    assert len(provider.requests) == 3
    assert_paired(conv)


async def test_iteration_limit():
    provider = FakeProvider([response(call("1", "read_file"))], repeat=True)
    conv = conversation()
    events = [e async for e in Agent(provider, new_default_registry()).run(conv)]
    assert len(provider.requests) == MAX_ITERATIONS
    assert any(e.notice == NOTICE_MAX_ITER for e in events)
    assert events[-1].done
    assert_paired(conv)


async def test_unknown_limit_and_reset():
    provider = FakeProvider([response(call("x", "missing"))], repeat=True)
    conv = conversation()
    events = [e async for e in Agent(provider, new_default_registry()).run(conv)]
    assert len(provider.requests) == MAX_UNKNOWN_RUN
    assert any(e.notice == NOTICE_UNKNOWN_TOOLS for e in events)
    assert_paired(conv)
    provider = FakeProvider(
        [
            *[response(call("x", "missing")) for _ in range(MAX_UNKNOWN_RUN - 1)],
            response(call("x", "missing"), call("r", "read_file")),
            *[response(call("x", "missing")) for _ in range(MAX_UNKNOWN_RUN - 1)],
            response(text="恢复完成"),
        ]
    )
    events = [
        e async for e in Agent(provider, new_default_registry()).run(conversation())
    ]
    assert events[-1].done
    assert not any(e.notice for e in events)
    assert len(provider.requests) == 2 * MAX_UNKNOWN_RUN


class ProbeTool:
    def __init__(self, name, read_only, execute):
        self._name = name
        self.read_only = read_only
        self.execute = execute

    def name(self):
        return self._name

    def description(self):
        return "测试插桩"

    def parameters(self):
        return {"type": "object", "properties": {}}


@pytest.mark.parametrize("repeat", range(5))
async def test_concurrent_batch(repeat):
    active = 0
    peak = 0
    order = []
    both_started = asyncio.Event()

    async def read(args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), 1)
        await asyncio.sleep(0.01 if json.loads(args)["n"] == 1 else 0)
        active -= 1
        order.append("read")
        return Result(args)

    async def write(args):
        assert active == 0
        order.append("write")
        return Result(args)

    registry = Registry()
    registry.register(ProbeTool("ro", True, read))
    registry.register(ProbeTool("rw", False, write))
    calls = [
        call("1", "ro", n=1),
        call("2", "ro", n=2),
        call("3", "rw"),
        call("4", "ro", n=3),
    ]
    provider = FakeProvider([response(*calls), response(text="完成")])
    conv = conversation()
    events = [e async for e in Agent(provider, registry).run(conv)]
    assert peak == 2
    assert order == ["read", "read", "write", "read"]
    assert [r.tool_call_id for r in conv.messages()[2].tool_results] == [
        "1",
        "2",
        "3",
        "4",
    ]
    assert [e.tool.args for e in events if e.tool and e.tool.phase == Phase.END] == [
        c.input for c in calls
    ]
    assert [e.tool.phase for e in events if e.tool][:4] == [
        Phase.START,
        Phase.START,
        Phase.END,
        Phase.END,
    ]


async def test_tool_cancel_pairs_all_calls_and_continues_without_leaks():
    entered = asyncio.Event()
    released = asyncio.Event()
    cancel = asyncio.Event()

    async def slow(args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    registry = Registry()
    registry.register(ProbeTool("slow", True, slow))
    provider = FakeProvider(
        [
            response(call("1", "slow"), call("2", "slow"), call("3", "missing")),
            response(text="继续成功"),
        ]
    )
    conv = conversation()
    agent = Agent(provider, registry)
    before = set(asyncio.all_tasks())

    async def consume():
        return [e async for e in agent.run(conv, Mode.NORMAL, cancel)]

    task = asyncio.create_task(consume())
    await asyncio.wait_for(entered.wait(), 1)
    cancel.set()
    await asyncio.wait_for(task, 1)
    assert released.is_set()
    assert_paired(conv)
    assert all(
        r.is_error and r.content == NOTICE_CANCELLED
        for r in conv.messages()[2].tool_results
    )
    assert conv.messages()[-1].content == NOTICE_CANCELLED
    conv.add_user("继续")
    assert [e async for e in agent.run(conv)][-1].done
    assert_paired(conv)
    assert set(asyncio.all_tasks()) <= before


@pytest.mark.parametrize("early_close", [False, True])
async def test_cancel_stalled_stream_or_close_generator(early_close):
    entered = asyncio.Event()
    closed = asyncio.Event()

    class StalledProvider:
        model = "test-model"

        async def stream(self, req):
            try:
                entered.set()
                yield StreamEvent(text="部分")
                await asyncio.Event().wait()
            finally:
                closed.set()

    cancel = asyncio.Event()
    conv = conversation()
    before = set(asyncio.all_tasks())

    async def consume():
        async with aclosing(
            Agent(StalledProvider(), Registry()).run(conv, Mode.NORMAL, cancel)
        ) as stream:
            async for event in stream:
                if event.text and early_close:
                    return

    task = asyncio.create_task(consume())
    await asyncio.wait_for(entered.wait(), 1)
    if not early_close:
        cancel.set()
    await asyncio.wait_for(task, 1)
    assert closed.is_set()
    assert conv.messages()[-1].content == NOTICE_CANCELLED
    assert_paired(conv)
    assert set(asyncio.all_tasks()) <= before


async def test_cancel_before_request():
    cancel = asyncio.Event()
    cancel.set()
    provider = FakeProvider([])
    conv = conversation()
    assert not [
        e async for e in Agent(provider, Registry()).run(conv, Mode.PLAN, cancel)
    ]
    assert not provider.requests
    assert conv.messages()[-1].content == NOTICE_CANCELLED


async def test_plan_definitions_reminder_and_execution_guard(tmp_path):
    path = tmp_path / "forbidden.txt"
    provider = FakeProvider(
        [
            response(call("w", "write_file", path=str(path), content="禁止")),
            response(text="计划完成"),
        ]
    )
    conv = conversation()
    events = [
        e async for e in Agent(provider, new_default_registry()).run(conv, Mode.PLAN)
    ]
    assert events[-1].done
    assert not path.exists()
    assert conv.messages()[2].tool_results[0].is_error
    assert [d.name for d in provider.requests[0].tools] == ["read_file", "glob", "grep"]
    assert provider.requests[0].reminder == plan_reminder(True)
    assert_paired(conv)


@pytest.mark.parametrize("kind", ["event", "exception", "truncated"])
async def test_stream_error_closes_history_and_allows_followup(kind):
    class BrokenProvider:
        model = "test-model"

        async def stream(self, req):
            yield StreamEvent(text="部分输出")
            if kind == "event":
                yield StreamEvent(err=RuntimeError("断流"))
            elif kind == "exception":
                raise RuntimeError("断流")

    conv = conversation()
    events = [e async for e in Agent(BrokenProvider(), Registry()).run(conv)]
    assert any(e.err is not None for e in events)
    assert any(e.notice == NOTICE_STREAM_ERR for e in events)
    assert not any(e.done for e in events)
    assert conv.messages()[-1].content == NOTICE_STREAM_ERR
    assert_paired(conv)
    conv.add_user("恢复")
    provider = FakeProvider([response(text="正常")])
    assert [e async for e in Agent(provider, Registry()).run(conv)][-1].done
    assert_paired(conv)


async def test_failed_continuation_pairs_existing_tool_history():
    provider = FakeProvider(
        [
            response(call("1", "missing")),
            [StreamEvent(err=RuntimeError("断流"))],
        ]
    )
    conv = conversation()
    events = [e async for e in Agent(provider, Registry()).run(conv)]
    assert events[-1].err
    assert_paired(conv)


async def test_empty_response_gets_nonempty_assistant():
    conv = conversation()
    events = [e async for e in Agent(FakeProvider([response()]), Registry()).run(conv)]
    assert events[-1].done
    assert conv.messages()[-1].content == EMPTY_REPLY
    assert any(e.text == EMPTY_REPLY for e in events)


async def test_request_modes_reminder_cadence_and_cache_passthrough(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("规划依据", encoding="utf-8")
    provider = FakeProvider(
        [
            *[response(call(str(i), "read_file", path=str(source))) for i in range(5)],
            [
                StreamEvent(text="计划完成"),
                StreamEvent(usage=Usage(10, 2, 123, 456, cache_write_reported=True)),
                StreamEvent(done=True),
            ],
            response(text="普通模式完成"),
            response(text="下一次规划完成"),
        ]
    )
    conv = conversation()
    agent = Agent(provider, new_default_registry(), "test-version")
    events = [e async for e in agent.run(conv, Mode.PLAN)]
    planned = list(provider.requests)
    assert [r.reminder for r in planned] == [
        plan_reminder(True),
        *[plan_reminder(False)] * 3,
        plan_reminder(True),
        plan_reminder(False),
    ]
    assert all(
        [d.name for d in r.tools] == ["read_file", "glob", "grep"] for r in planned
    )
    assert all(r.system.stable == build_system_prompt() for r in planned)
    assert all(
        "test-version" in r.system.environment and "test-model" in r.system.environment
        for r in planned
    )
    assert len({r.system.environment for r in planned}) == 1
    assert all("<system-reminder>" not in m.content for m in conv.messages())
    assert [(e.usage.cache_write, e.usage.cache_read) for e in events if e.usage][
        -1
    ] == (123, 456)
    assert [e.usage for e in events if e.usage][-1].cache_write_reported
    conv.add_user("继续执行")
    assert [e async for e in agent.run(conv)][-1].done
    normal = provider.requests[-1]
    assert normal.system.stable == planned[0].system.stable
    assert len(normal.tools) == 6 and normal.reminder == ""
    conv.add_user("重新规划")
    assert [e async for e in agent.run(conv, Mode.PLAN)][-1].done
    assert provider.requests[-1].reminder == plan_reminder(True)
    assert_paired(conv)


async def test_environment_collection_does_not_block_loop(monkeypatch):
    import threading

    from termcode import prompt

    entered, release = threading.Event(), threading.Event()

    def slow(version, model):
        entered.set()
        release.wait(timeout=2)
        return Environment("/tmp", "test", "", "", version, model)

    monkeypatch.setattr(prompt, "gather_environment", slow)
    provider = FakeProvider([response(text="完成")])

    async def consume():
        return [e async for e in Agent(provider, Registry()).run(conversation())]

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(asyncio.to_thread(entered.wait, 1), 1.5)
        assert entered.is_set() and not provider.requests
        # 到达此处说明采集阻塞期间事件循环仍能调度其他工作。
        release.set()
        assert (await asyncio.wait_for(task, 2))[-1].done
    finally:
        release.set()
        await task
