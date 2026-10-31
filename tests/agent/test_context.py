"""真实 Agent 主循环的压缩、工具追踪、用量和串行执行。"""

import asyncio
import json
from pathlib import Path

import pytest

from termcode.agent import Agent, CompactPhase, SessionRuntime
from termcode.compact import new_session_context
from termcode.compact.recovery import render_tools_block
from termcode.compact.summary_prompt import SUMMARY_TITLES
from termcode.compact.token import estimate_tokens
from termcode.conversation import Conversation
from termcode.llm import (
    PromptTooLongError,
    StreamEvent,
    ToolCall,
    ToolResult,
    Usage,
)
from termcode.permission import Mode
from termcode.prompt import Environment
from termcode.tool import new_default_registry

SUMMARY = "<summary>" + "\n".join(SUMMARY_TITLES) + "</summary>"


class Provider:
    name = "fake"
    model = "test-model"

    def __init__(self, scripts=()):
        self.scripts = list(scripts)
        self.requests = []

    async def stream(self, req):
        self.requests.append(req)
        for event in self.scripts.pop(0):
            yield event

    async def aclose(self):
        pass


def summary_response():
    return [
        StreamEvent(text=SUMMARY),
        StreamEvent(usage=Usage(9000, 9000)),
        StreamEvent(done=True),
    ]


def answer(text="完成", usage=None):
    return [
        StreamEvent(text=text),
        StreamEvent(usage=usage or Usage(100, 50)),
        StreamEvent(done=True),
    ]


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "termcode.prompt.gather_environment",
        lambda v, m: Environment(str(tmp_path), "test", "2026-10-31", "", v, m),
    )


def make_agent(provider, tmp_path, **kwargs):
    runtime = SessionRuntime(session=new_session_context(str(tmp_path)), **kwargs)
    return Agent(provider, new_default_registry(), runtime=runtime)


def conversation():
    conv = Conversation()
    conv.add_user("请修复文件并测试")
    return conv


@pytest.mark.parametrize("again", [False, True])
async def test_emergency_compact_one_retry(tmp_path, again):
    ptl = PromptTooLongError("full")
    provider = Provider(
        [
            [StreamEvent(err=ptl)],
            summary_response(),
            [StreamEvent(err=ptl)] if again else answer(),
        ]
    )
    agent = make_agent(provider, tmp_path)
    conv = conversation()
    events = [e async for e in agent.run(conv)]
    assert [e.compact.phase for e in events if e.compact] == [
        CompactPhase.BEFORE_EMERGENCY,
        CompactPhase.AFTER_EMERGENCY,
    ]
    assert len(provider.requests) == 3
    assert provider.requests[1].tools is None
    assert provider.requests[0].tools is provider.requests[2].tools
    assert events[-1].err is ptl if again else events[-1].done
    assert agent.runtime.auto_tracking.consecutive_failures == 0


async def test_emergency_summary_failure_emits_error(tmp_path):
    provider = Provider(
        [
            [StreamEvent(err=PromptTooLongError("full"))],
            [StreamEvent(err=RuntimeError("500"))],
        ]
    )
    agent = make_agent(provider, tmp_path)
    events = [e async for e in agent.run(conversation())]
    phases = [e.compact for e in events if e.compact]
    assert phases[-1].phase == CompactPhase.AFTER_EMERGENCY
    assert phases[-1].err is not None
    assert events[-1].err is not None
    assert len(provider.requests) == 2


async def test_emergency_still_too_big_does_not_retry(tmp_path):
    huge = "<summary>" + "x" * 800000 + "</summary>"
    provider = Provider(
        [
            [StreamEvent(err=PromptTooLongError("full"))],
            [StreamEvent(text=huge), StreamEvent(done=True)],
        ]
    )
    agent = make_agent(provider, tmp_path)
    events = [e async for e in agent.run(conversation())]
    assert isinstance(events[-1].err, PromptTooLongError)
    assert len(provider.requests) == 2
    assert agent.runtime.usage_anchor == agent.runtime.anchor_msg_len == 0


@pytest.mark.parametrize("mode", [Mode.DEFAULT, Mode.PLAN])
async def test_auto_events_and_same_tool_list_reference(tmp_path, monkeypatch, mode):
    import termcode.agent.agent as module
    import termcode.compact.layer2 as layer2

    provider = Provider([summary_response(), answer()])
    agent = make_agent(provider, tmp_path, usage_anchor=180000, anchor_msg_len=20)
    conv = conversation()
    references = []
    snapshots = []
    original = module.manage_context
    original_recovery = layer2.build_recovery_attachment

    async def manage(in_):
        references.append(in_.tool_defs)
        return await original(in_)

    def recovery(snapshot, defs):
        snapshots.append(defs)
        return original_recovery(snapshot, defs)

    monkeypatch.setattr(module, "manage_context", manage)
    monkeypatch.setattr(layer2, "build_recovery_attachment", recovery)
    events = [e async for e in agent.run(conv, mode)]
    compact = [e.compact for e in events if e.compact]
    assert [e.phase for e in compact] == [
        CompactPhase.BEFORE_AUTO,
        CompactPhase.AFTER_AUTO,
    ]
    assert compact[1].before > compact[1].after
    defs = provider.requests[-1].tools
    assert references[0] is snapshots[0] is defs
    expected = (
        agent._registry.read_only_definitions()
        if mode == Mode.PLAN
        else agent._registry.definitions()
    )
    assert defs == expected
    assert render_tools_block(defs) in provider.requests[-1].messages[0].content
    assert agent.runtime.usage_anchor == 150
    assert agent.runtime.anchor_msg_len == conv.length()


async def test_auto_failure_keeps_conversation_running(tmp_path):
    provider = Provider([[StreamEvent(err=RuntimeError("500"))], answer()])
    agent = make_agent(provider, tmp_path, usage_anchor=180000, anchor_msg_len=1)
    events = [e async for e in agent.run(conversation())]
    assert events[-1].done
    assert [e.compact for e in events if e.compact][-1].err is not None
    assert agent.runtime.auto_tracking.consecutive_failures == 1


async def test_no_notice_if_layer1_saves_enough(tmp_path):
    provider = Provider([answer()])
    agent = make_agent(provider, tmp_path, context_window=50000)
    conv = conversation()
    conv.add_assistant_with_tool_calls("", [ToolCall("big", "read_file", "{}")])
    conv.add_tool_results([ToolResult("big", "x" * 80000)])
    events = [e async for e in agent.run(conv)]
    assert not any(e.compact for e in events)
    assert (Path(agent.runtime.session.spill_dir) / "big").stat().st_size == 80000


async def test_read_file_recovery_records_clean_bytes_before_next_request(tmp_path):
    path = tmp_path / "file.txt"
    path.write_bytes(("中文\r\n" * 10).encode())
    call = ToolCall("a", "read_file", json.dumps({"path": str(path)}))
    provider = Provider(
        [
            [
                StreamEvent(tool_calls=[call]),
                StreamEvent(usage=Usage(100, 20)),
                StreamEvent(done=True),
            ],
            answer(),
        ]
    )
    agent = make_agent(provider, tmp_path)
    records = []
    original_stream = provider.stream

    async def stream(req):
        if len(provider.requests) == 1:
            records.extend(agent.runtime.recovery.snapshot())
        async for event in original_stream(req):
            yield event

    provider.stream = stream
    events = [e async for e in agent.run(conversation())]
    assert events[-1].done
    assert len(records) == 1
    assert records[0].path == str(path.resolve())
    assert records[0].content.encode() == path.read_bytes()


async def test_usage_anchor_is_replaced_and_manual_clears_it(tmp_path):
    provider = Provider(
        [
            answer(usage=Usage(900, 50, 20, 30)),
            answer(usage=Usage(1300, 100, 40, 60)),
            answer(usage=Usage(1900, 200, 40, 60)),
            summary_response(),
        ]
    )
    agent = make_agent(provider, tmp_path)
    conv = Conversation()
    for expected in (1000, 1500, 2200):
        conv.add_user("继续")
        assert [e async for e in agent.run(conv)][-1].done
        assert agent.runtime.usage_anchor == expected
        assert agent.runtime.anchor_msg_len == conv.length()
    before, after = await agent.run_force_compact(conv, agent._registry.definitions())
    assert before == 2200
    assert after == estimate_tokens(0, conv.messages(), 0)
    assert agent.runtime.usage_anchor == agent.runtime.anchor_msg_len == 0


async def test_manual_and_run_are_serialized(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()
    finished = []

    class Slow(Provider):
        async def stream(self, req):
            if req.tools is not None:
                started.set()
                await release.wait()
                finished.append("run")
            else:
                finished.append("compact")
            async for event in super().stream(req):
                yield event

    provider = Slow([answer(), summary_response()])
    agent = make_agent(provider, tmp_path)
    conv = conversation()

    async def run():
        return [e async for e in agent.run(conv)]

    worker = asyncio.create_task(run())
    await asyncio.wait_for(started.wait(), 5)
    compact = asyncio.create_task(
        agent.run_force_compact(conv, agent._registry.definitions())
    )
    await asyncio.sleep(0.03)
    assert not compact.done() and not finished
    release.set()
    await asyncio.wait_for(asyncio.gather(worker, compact), 5)
    assert finished == ["run", "compact"]


async def test_long_session_30_tool_rounds(tmp_path):
    path = tmp_path / "bulk.txt"
    path.write_text("x" * 30000, encoding="utf-8")

    class LongProvider(Provider):
        counter = 0
        summaries = 0

        async def stream(self, req):
            self.requests.append(req)
            if req.tools is None:
                self.summaries += 1
                yield StreamEvent(text=SUMMARY)
            else:
                self.counter += 1
                if self.counter % 2:
                    yield StreamEvent(
                        tool_calls=[
                            ToolCall(
                                str(self.counter),
                                "read_file",
                                json.dumps({"path": str(path)}),
                            )
                        ]
                    )
                else:
                    yield StreamEvent(text="这一轮完成")
                yield StreamEvent(usage=Usage(estimate_tokens(0, req.messages, 0), 20))
            yield StreamEvent(done=True)

    provider = LongProvider()
    registry = new_default_registry()
    agent = Agent(provider, registry, runtime=SessionRuntime(context_window=50000))
    conv = Conversation()
    for _ in range(30):
        conv.add_user("连续读取并分析")
        events = [e async for e in agent.run(conv)]
        assert events[-1].done and not any(e.err for e in events)
        assert not [str(e.compact.err) for e in events if e.compact and e.compact.err]
    assert provider.counter == 60
    assert provider.summaries > 0
    assert conv.length() < 20


async def test_no_compact_event_below_threshold_25_turns(tmp_path):
    provider = Provider([answer() for _ in range(25)])
    agent = make_agent(provider, tmp_path)
    conv = Conversation()
    for _ in range(25):
        conv.add_user("继续")
        events = [e async for e in agent.run(conv)]
        assert events[-1].done and not any(e.compact for e in events)


async def test_runtime_anchor_reset_even_when_cancel_arrives_at_commit(
    tmp_path, monkeypatch
):
    import termcode.agent.agent as module

    cancel = asyncio.Event()
    provider = Provider([summary_response()])
    agent = make_agent(provider, tmp_path, usage_anchor=180000, anchor_msg_len=1)
    original = module.manage_context

    async def manage(in_):
        out = await original(in_)
        cancel.set()
        return out

    monkeypatch.setattr(module, "manage_context", manage)
    _ = [e async for e in agent.run(conversation(), cancel=cancel)]
    assert agent.runtime.usage_anchor == agent.runtime.anchor_msg_len == 0


async def test_seven_reads_restore_latest_five_clean_files_and_schemas(tmp_path):
    paths = [tmp_path / f"read-{i}.txt" for i in range(7)]
    for i, path in enumerate(paths):
        path.write_text(str(i) * 18000, encoding="utf-8")
    calls = [
        ToolCall(str(i), "read_file", json.dumps({"path": str(path)}))
        for i, path in enumerate(paths)
    ]
    summary = SUMMARY.replace(
        "## 6 所有用户消息原文", "## 6 所有用户消息原文\n请修复文件并测试"
    )
    provider = Provider(
        [
            [
                StreamEvent(tool_calls=calls),
                StreamEvent(usage=Usage(100, 50)),
                StreamEvent(done=True),
            ],
            answer(),
            [StreamEvent(text=summary), StreamEvent(done=True)],
            answer(),
        ]
    )
    agent = make_agent(provider, tmp_path)
    conv = conversation()
    assert [e async for e in agent.run(conv)][-1].done
    snapshot = agent.runtime.recovery.snapshot()
    assert len(snapshot) == 7
    for record in snapshot:
        assert record.content.encode() == await asyncio.to_thread(
            Path(record.path).read_bytes
        )
    await agent.run_force_compact(conv, agent._registry.definitions())
    conv.add_user("继续")
    assert [e async for e in agent.run(conv)][-1].done
    req = provider.requests[-1]
    combined = req.messages[0].content
    assert all(record.path in combined for record in snapshot[:5])
    assert all(record.path not in combined for record in snapshot[5:])
    positions = [combined.index(record.path) for record in snapshot[:5]]
    assert positions == sorted(positions)
    assert combined.count("(content truncated)") == 5
    assert render_tools_block(req.tools) in combined
    assert "请修复文件并测试" in combined.split("## 6", 1)[1].split("## 7", 1)[0]
