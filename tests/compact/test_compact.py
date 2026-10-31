"""阈值使用落盘后的消息，手动和紧急路径绕过自动熔断。"""

import pytest

from termcode.compact import CompactError, TriggerKind, manage_context
from termcode.compact.token import estimate_tokens
from termcode.llm import Message, PromptTooLongError, StreamEvent, ToolResult


@pytest.mark.parametrize("anchor,calls", [(166999, 0), (167000, 1), (167001, 1)])
async def test_manage_context_auto_threshold(make_input, anchor, calls):
    in_ = make_input(
        [Message("user", "hello")],
        usage_anchor=anchor,
        anchor_msg_len=1,
        estimated_token=anchor,
    )
    out = await manage_context(in_)
    assert len(in_.provider.requests) == calls
    assert out.before_tokens == anchor and out.summarized == bool(calls)


async def test_manage_context_auto_uses_layer1_output(make_input):
    msgs = [Message("tool", tool_results=[ToolResult("a", "x" * 60000)])]
    in_ = make_input(msgs, context_window=50000, estimated_token=18000)
    out = await manage_context(in_)
    assert not out.summarized and not in_.provider.requests
    assert "[content offloaded]" in in_.conv.messages()[0].tool_results[0].content


async def test_manage_context_auto_skipped_when_tripped(make_input):
    in_ = make_input([Message("user", "hello")], usage_anchor=200000, anchor_msg_len=1)
    for _ in range(3):
        in_.auto_tracking.record_failure()
    await manage_context(in_)
    assert not in_.provider.requests


@pytest.mark.parametrize("trigger", [TriggerKind.MANUAL, TriggerKind.EMERGENCY])
async def test_manage_context_force_bypasses_threshold_and_breaker(make_input, trigger):
    in_ = make_input([Message("user", "hello")], trigger=trigger, estimated_token=1000)
    for _ in range(3):
        in_.auto_tracking.record_failure()
    out = await manage_context(in_)
    assert out.summarized and out.before_tokens == 1000
    assert out.after_tokens == estimate_tokens(0, in_.conv.messages(), 0)
    assert in_.auto_tracking.consecutive_failures == 3


@pytest.mark.parametrize(
    "trigger,spilled", [(TriggerKind.MANUAL, False), (TriggerKind.EMERGENCY, True)]
)
async def test_manual_skips_layer1_and_emergency_runs_it(make_input, trigger, spilled):
    in_ = make_input(
        [Message("tool", tool_results=[ToolResult("big", "x" * 60000)])],
        trigger=trigger,
    )
    await manage_context(in_)
    assert in_.replacement.has_seen("big") == spilled


@pytest.mark.parametrize(
    "trigger,failures",
    [(TriggerKind.AUTO, 1), (TriggerKind.MANUAL, 0), (TriggerKind.EMERGENCY, 0)],
)
@pytest.mark.parametrize("ptl", [False, True])
async def test_summary_failure_counts_only_auto(
    make_input, fake_compact_provider, trigger, failures, ptl
):
    err = PromptTooLongError("full") if ptl else RuntimeError("500")
    provider = fake_compact_provider([[StreamEvent(err=err)] for _ in range(10)])
    in_ = make_input(
        [Message("user", str(i)) for i in range(3)],
        provider=provider,
        trigger=trigger,
        usage_anchor=200000,
        anchor_msg_len=3,
    )
    before = in_.conv.messages()
    with pytest.raises(CompactError):
        await manage_context(in_)
    assert in_.conv.messages() == before
    assert in_.auto_tracking.consecutive_failures == failures


async def test_auto_failure_trip_then_manual_success(make_input, fake_compact_provider):
    provider = fake_compact_provider(
        [[StreamEvent(err=RuntimeError("500"))] for _ in range(3)]
        + [[StreamEvent(text="<summary>恢复</summary>")]]
    )
    in_ = make_input(
        [Message("user", "hello")],
        provider=provider,
        usage_anchor=200000,
        anchor_msg_len=1,
    )
    for _ in range(3):
        with pytest.raises(CompactError):
            await manage_context(in_)
    await manage_context(in_)
    assert len(provider.requests) == 3
    in_.trigger = TriggerKind.MANUAL
    await manage_context(in_)
    assert len(provider.requests) == 4
    assert in_.auto_tracking.consecutive_failures == 3


async def test_auto_success_resets_consecutive_failures(make_input):
    in_ = make_input([Message("user", "hello")], usage_anchor=200000, anchor_msg_len=1)
    in_.auto_tracking.record_failure()
    in_.auto_tracking.record_failure()
    await manage_context(in_)
    assert in_.auto_tracking.consecutive_failures == 0


async def test_small_window_skips_auto(make_input):
    in_ = make_input([Message("user", "hello")], context_window=33000)
    await manage_context(in_)
    assert not in_.provider.requests
