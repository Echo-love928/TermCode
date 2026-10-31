"""近期原文配对、无工具摘要及摘要输入超长重试。"""

import math

import pytest

from termcode.compact import layer2
from termcode.compact.summary_prompt import SUMMARY_TITLES
from termcode.compact.token import message_chars
from termcode.llm import Message, PromptTooLongError, StreamEvent, ToolCall, ToolResult


def test_pick_recent_tail_boundary_and_pair_fix():
    msgs = [Message("user", "old") for _ in range(10)] + [
        Message("assistant", tool_calls=[ToolCall("a", "read", "{}")]),
        Message("tool", tool_results=[ToolResult("a", "a" * 35000)]),
        Message("assistant", "ok"),
        Message("user", "继续"),
        Message("assistant", tool_calls=[ToolCall("b", "read", "{}")]),
        Message("tool", tool_results=[ToolResult("b", "ok")]),
    ]
    recent = layer2.pick_recent_tail(msgs)
    assert len(recent) == 6
    assert recent[0].role == "assistant" and recent[0].tool_calls[0].id == "a"
    assert message_chars(recent) / 3.5 >= 10000
    assert len(recent) <= len(msgs)
    assert layer2.pick_recent_tail([]) == []
    # 不足任一下界时保留所有可用消息。
    short = [Message("user", "x") for _ in range(8)]
    assert layer2.pick_recent_tail(short) == short
    few = [Message("user", "x" * 70000), Message("assistant", "ok")]
    assert layer2.pick_recent_tail(few) == few


def test_join_after_summary_avoids_consecutive_user():
    joined = layer2._join_after_summary(
        Message("user", "摘要"), [Message("user", "原话")]
    )
    assert [m.role for m in joined] == ["user", "assistant", "user"]


def test_group_by_user_turn():
    msgs = [
        Message("user", "u"),
        Message("assistant", "a"),
        Message("tool"),
        Message("user", "u"),
        Message("assistant", "a"),
    ]
    assert [len(g) for g in layer2.group_by_user_turn(msgs)] == [3, 2]
    assert layer2.group_by_user_turn([]) == []
    assert (
        len(
            layer2.group_by_user_turn([Message("assistant", "a"), Message("user", "u")])
        )
        == 2
    )


async def test_summarize_once_ptl_passthrough(make_input, fake_compact_provider):
    err = PromptTooLongError("full")
    in_ = make_input(
        [Message("user", "hello")],
        provider=fake_compact_provider([[StreamEvent(err=err)]]),
    )
    with pytest.raises(PromptTooLongError) as caught:
        await layer2.summarize_once(in_, in_.conv.messages())
    assert caught.value is err


async def test_run_summary_basic(make_input, fake_compact_provider):
    users = ["请修复 A", "完成后测试"]
    summary = "\n".join(
        t + ("\n" + "\n".join(users) if i == 5 else "\n事实")
        for i, t in enumerate(SUMMARY_TITLES)
    )
    provider = fake_compact_provider(
        [
            [
                StreamEvent(
                    text="<analysis>不保留</analysis><summary>" + summary + "</summary>"
                )
            ]
        ]
    )
    in_ = make_input(
        [
            Message("user", users[0]),
            Message("assistant", "好的"),
            Message("user", users[1]),
        ],
        provider=provider,
    )
    messages = await layer2.run_summary(in_)
    text = messages[0].content
    assert messages[0].role == "user" and "不保留" not in text
    assert all(title in text for title in SUMMARY_TITLES)
    assert all(user in text.split("## 6", 1)[1].split("## 7", 1)[0] for user in users)
    assert all(
        title in text for title in ("最近读过的文件", "当前可用工具", "边界提示")
    )
    assert provider.requests[0].tools is None
    assert provider.requests[0].max_tokens == 20000
    assert messages[1].role == "assistant" and messages[2].content == users[0]


@pytest.mark.parametrize("failures", [3, 5])
async def test_ptl_retry_drop_schedule(make_input, fake_compact_provider, failures):
    msgs = [Message("user", f"TURN-{i:02d}") for i in range(20)]
    provider = fake_compact_provider(
        [[StreamEvent(err=PromptTooLongError("full"))] for _ in range(failures)]
        + [[StreamEvent(text="<summary>ok</summary>")]]
    )
    in_ = make_input(msgs, provider=provider)
    await layer2.run_summary(in_)
    counts = [
        sum(f"user: TURN-{i:02d}" in req.messages[0].content for i in range(20))
        for req in provider.requests
    ]
    expected = [20]
    for retry in range(failures):
        expected.append(
            expected[-1] - (1 if retry < 3 else math.ceil(expected[-1] * 0.2))
        )
    assert counts == expected


async def test_ptl_retry_stops_before_empty_messages(make_input, fake_compact_provider):
    provider = fake_compact_provider(
        [[StreamEvent(err=PromptTooLongError("full"))]] * 10
    )
    in_ = make_input(
        [Message("user", f"TURN-{i}") for i in range(3)], provider=provider
    )
    with pytest.raises(PromptTooLongError):
        await layer2.run_summary(in_)
    assert len(provider.requests) == 3
    assert all("user: TURN-" in req.messages[0].content for req in provider.requests)


async def test_summary_preflight_drops_before_network(
    make_input, fake_compact_provider
):
    provider = fake_compact_provider([[StreamEvent(text="<summary>ok</summary>")]])
    msgs = [Message("user", "x" * 100000), Message("user", "最新请求")]
    in_ = make_input(msgs, provider=provider, context_window=50000)
    await layer2.run_summary(in_)
    assert len(provider.requests) == 1
    assert "x" * 100 not in provider.requests[0].messages[0].content
    assert "最新请求" in provider.requests[0].messages[0].content


async def test_ptl_non_ptl_error_does_not_retry(make_input, fake_compact_provider):
    provider = fake_compact_provider(
        [
            [StreamEvent(err=PromptTooLongError("full"))],
            [StreamEvent(err=RuntimeError("500"))],
        ]
    )
    in_ = make_input([Message("user", str(i)) for i in range(10)], provider=provider)
    with pytest.raises(RuntimeError, match="500"):
        await layer2.run_summary(in_)
    assert len(provider.requests) == 2


async def test_empty_summary_is_failure(make_input, fake_compact_provider):
    in_ = make_input(
        [Message("user", "hello")],
        provider=fake_compact_provider(
            [[StreamEvent(text="<analysis>only</analysis><summary> </summary>")]]
        ),
    )
    with pytest.raises(ValueError, match="可用摘要"):
        await layer2.run_summary(in_)
