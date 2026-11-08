"""验证分派顺序、拦截、并发占位与后台任务回收。"""

import asyncio

from termcode.hook import Engine, Event
from termcode.hook.event import is_blocking
from termcode.hook.executor import ExecutionResult
from termcode.hook.rule import PromptAction, Rule


def test_event_set():
    assert len(Event) == 11
    assert {event for event in Event if is_blocking(event)} == {
        Event.PRE_TOOL_USE,
        Event.USER_PROMPT_SUBMIT,
    }


async def test_dispatch_blocking(monkeypatch, capsys):
    names = ["first", "second", "failure", "block", "last"]
    event = Event.PRE_TOOL_USE
    engine = Engine([Rule(name, event, PromptAction(name)) for name in names], [])
    seen = []

    async def run(rule, payload, *, blocking):
        assert blocking and payload["event"] == "PreToolUse"
        seen.append(rule.name)
        if rule.name == "failure":
            return ExecutionResult(err=ValueError("first line\nsecond line"))
        if rule.name == "block":
            return ExecutionResult(blocked=True, reason="policy")
        return ExecutionResult(prompt=rule.name)

    monkeypatch.setattr(engine._executor, "run", run)
    result = await engine.dispatch(event, {})
    assert result.block_message() == "[hook block] policy"
    assert result.blocked and result.injected_prompts == ["first", "second"]
    assert seen == names[:-1]
    assert (
        "[hook failure] PreToolUse failed: first line second line"
        in capsys.readouterr().err
    )


async def test_only_once_concurrent_and_reset(monkeypatch):
    event = Event.STOP
    engine = Engine([Rule("once", event, PromptAction("one"), only_once=True)], [])
    entered, release = asyncio.Event(), asyncio.Event()

    async def run(rule, payload, *, blocking):
        entered.set()
        await release.wait()
        return ExecutionResult(prompt="one", blocked=True)

    monkeypatch.setattr(engine._executor, "run", run)
    first = asyncio.create_task(engine.dispatch(event, {}))
    await entered.wait()
    assert (await engine.dispatch(event, {})).injected_prompts == []
    release.set()
    result = await first
    assert result.injected_prompts == ["one"] and not result.blocked
    await engine.reset_for_new_session()
    assert (await engine.dispatch(event, {})).injected_prompts == ["one"]


async def test_background_failure_and_cleanup(monkeypatch, capsys):
    event = Event.POST_TOOL_USE
    rules = [
        Rule("failure", event, PromptAction(""), asyncio_mode=True),
        Rule("slow", event, PromptAction(""), asyncio_mode=True),
    ]
    engine = Engine(rules, [])
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def run(rule, payload, *, blocking):
        assert not blocking
        if rule.name == "failure":
            return ExecutionResult(err=ValueError("failed in background"))
        try:
            entered.set()
            await asyncio.Future()
        finally:
            stopped.set()

    monkeypatch.setattr(engine._executor, "run", run)
    result = await engine.dispatch(event, {})
    assert not result.blocked and not result.injected_prompts
    await entered.wait()
    assert "failed in background" in capsys.readouterr().err
    await engine.aclose()
    assert stopped.is_set()
