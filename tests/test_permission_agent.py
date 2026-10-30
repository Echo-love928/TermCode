"""权限与真实编排集成：允许、拒绝、审批、取消及并发。"""

import asyncio
from pathlib import Path

import pytest
from test_agent import (
    FakeProvider,
    ProbeTool,
    assert_paired,
    call,
    conversation,
    response,
)

from termcode.agent import Agent, ApprovalRequest, Phase
from termcode.permission import Decision, Mode, Outcome, new_engine
from termcode.permission.rule import Rule
from termcode.tool import Registry, Result, new_default_registry


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    return new_engine(str(tmp_path))[0]


@pytest.mark.parametrize("outcome", list(Outcome))
async def test_approval_choices_loop_feedback_and_reload(engine, outcome):
    tool = call("write", "write_file", path="new.txt", content="内容")
    provider = FakeProvider([response(tool), response(text="继续完成")])
    agent = Agent(provider, new_default_registry(), engine=engine)
    conv = conversation()
    async for event in agent.run(conv):
        if isinstance(event, ApprovalRequest):
            assert event.name == "write_file" and "需确认" in event.reason
            event.respond.set_result(outcome)
    assert (await asyncio.to_thread(Path("new.txt").exists)) == (
        outcome != Outcome.DENY_ONCE
    )
    assert conv.messages()[2].tool_results[0].is_error == (outcome == Outcome.DENY_ONCE)
    assert len(provider.requests) == 2
    assert (await asyncio.to_thread(Path(engine.local_path).exists)) == (
        outcome == Outcome.ALLOW_FOREVER
    )
    if outcome == Outcome.ALLOW_FOREVER:
        assert new_engine(engine.root)[0].check(Mode.DEFAULT, tool)[0] == Decision.ALLOW
    assert_paired(conv)


@pytest.mark.parametrize("task_cancel", [False, True, "close"])
async def test_cancel_while_approving_no_tasks_and_can_resume(engine, task_cancel):
    provider = FakeProvider(
        [
            response(call("w", "write_file", path="never.txt", content="never")),
            response(text="恢复成功"),
        ]
    )
    conv = conversation()
    cancel = asyncio.Event()
    agent = Agent(provider, new_default_registry(), engine=engine)
    before = set(asyncio.all_tasks())
    entered = asyncio.Event()
    approval = None

    async def consume():
        nonlocal approval
        stream = agent.run(conv, cancel=cancel)
        try:
            async for event in stream:
                if isinstance(event, ApprovalRequest):
                    approval = event
                    entered.set()
                    if task_cancel == "close":
                        return
        finally:
            await stream.aclose()

    task = asyncio.create_task(consume())
    await asyncio.wait_for(entered.wait(), 2)
    if task_cancel is True:
        task.cancel()
    elif task_cancel is False:
        cancel.set()
    await asyncio.gather(task, return_exceptions=True)
    assert approval.respond.done() and not (
        await asyncio.to_thread(Path("never.txt").exists)
    )
    assert_paired(conv)
    assert set(asyncio.all_tasks()) <= before
    conv.add_user("继续")
    assert [e async for e in agent.run(conv)][-1].done
    assert_paired(conv)


async def test_readonly_mixed_batch_concurrent_and_ordered(engine):
    active, peak = 0, 0
    both = asyncio.Event()

    async def execute(args):
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        if active == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 2)
        active -= 1
        return Result("ok")

    registry = Registry()
    registry.register(ProbeTool("read_file", True, execute))
    provider = FakeProvider(
        [
            response(
                call("deny", "read_file", path="../outside"),
                call("one", "read_file", path="one"),
                call("two", "read_file", path="two"),
            ),
            response(text="完成"),
        ]
    )
    conv = conversation()
    events = [e async for e in Agent(provider, registry, engine=engine).run(conv)]
    assert not any(isinstance(e, ApprovalRequest) for e in events)
    assert peak == 2
    results = conv.messages()[2].tool_results
    assert [r.tool_call_id for r in results] == ["deny", "one", "two"]
    assert [r.is_error for r in results] == [True, False, False]
    assert [e.tool.phase for e in events if e.tool] == [Phase.START] * 3 + [
        Phase.END
    ] * 3
    assert_paired(conv)


async def test_persist_failure_logs_without_secret_and_still_executes(
    engine, monkeypatch, caplog
):
    def fail(*args):
        raise OSError("TEST_SECRET")

    monkeypatch.setattr(engine, "persist_local_allow", fail)
    provider = FakeProvider(
        [
            response(call("w", "write_file", path="ok", content="yes")),
            response(text="完成"),
        ]
    )
    async for event in Agent(provider, new_default_registry(), engine=engine).run(
        conversation()
    ):
        if isinstance(event, ApprovalRequest):
            event.respond.set_result(Outcome.ALLOW_FOREVER)
    assert (await asyncio.to_thread(Path("ok").read_text)) == "yes"
    assert "TEST_SECRET" not in caplog.text and "保存失败" in caplog.text


async def test_readonly_ask_is_denied_and_unknown_is_never_executed(engine):
    engine.local.ask.append(Rule("Read"))
    provider = FakeProvider(
        [
            response(call("r", "read_file", path="x"), call("u", "unknown")),
            response(text="继续"),
        ]
    )
    conv = conversation()
    events = [
        e
        async for e in Agent(provider, new_default_registry(), engine=engine).run(
            conv, Mode.BYPASS
        )
    ]
    assert not any(isinstance(e, ApprovalRequest) for e in events)
    assert all(r.is_error for r in conv.messages()[2].tool_results)
