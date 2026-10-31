"""结构化摘要、超长重试与近期原文拼接。"""

from __future__ import annotations

import math
from contextlib import aclosing
from typing import TYPE_CHECKING

from termcode.llm import Message, PromptTooLongError, Request

from .const import (
    ESTIMATE_CHARS_PER_TOKEN,
    MANUAL_SAFETY_MARGIN,
    PTL_DROP_PERCENTAGE,
    PTL_RETRY_LIMIT,
    RECENT_KEEP_MESSAGES,
    RECENT_KEEP_TOKENS,
    SUMMARY_RESERVE,
)
from .recovery import build_recovery_attachment
from .summary_prompt import build_summary_prompt, extract_summary
from .token import estimate_tokens, message_chars

if TYPE_CHECKING:
    from .compact import ManageInput


def pick_recent_tail(msgs: list[Message]) -> list[Message]:
    if not msgs:
        return []
    size = 0
    start = 0
    for i in range(len(msgs) - 1, -1, -1):
        start = i
        size += message_chars([msgs[i]])
        if (
            size / ESTIMATE_CHARS_PER_TOKEN >= RECENT_KEEP_TOKENS
            and len(msgs) - i >= RECENT_KEEP_MESSAGES
        ):
            break
    # 配对不能跨越截断点；只在原消息中前移，不生成工具占位。
    while start > 0 and msgs[start].role == "tool":
        start -= 1
    return list(msgs[start:])


def _join_after_summary(summary: Message, recent: list[Message]) -> list[Message]:
    while recent and recent[0].role == "tool":
        recent = recent[1:]
    bridge = (
        [Message("assistant", "（已加载上下文摘要与恢复信息。请继续。）")]
        if recent and recent[0].role == "user"
        else []
    )
    return [summary, *bridge, *recent]


def group_by_user_turn(msgs: list[Message]) -> list[list[Message]]:
    groups: list[list[Message]] = []
    for msg in msgs:
        if msg.role == "user" or not groups:
            groups.append([])
        groups[-1].append(msg)
    return groups


async def summarize_once(in_: ManageInput, msgs: list[Message]) -> str:
    prompt = build_summary_prompt(msgs)
    if estimate_tokens(0, prompt, 0) > (
        in_.context_window - SUMMARY_RESERVE - MANUAL_SAFETY_MARGIN
    ):
        raise PromptTooLongError("摘要输入超出预检预算")
    parts = []
    async with aclosing(
        in_.provider.stream(Request(prompt, tools=None, max_tokens=SUMMARY_RESERVE))
    ) as stream:
        async for event in stream:
            if event.err is not None:
                raise event.err
            if event.tool_calls:
                raise ValueError("摘要请求意外返回工具调用")
            if event.text:
                parts.append(event.text)
    # 摘要 usage 不更新主对话锚点，由 Agent 在替换历史后重置。
    text = extract_summary("".join(parts))
    if not text.strip():
        raise ValueError("模型未返回可用摘要")
    return text


async def ptl_retry(in_: ManageInput, msgs: list[Message], first_err: Exception) -> str:
    groups = group_by_user_turn(msgs)
    last_err = first_err
    retries = 0
    while groups:
        drop = (
            1
            if retries < PTL_RETRY_LIMIT
            else max(1, math.ceil(len(groups) * PTL_DROP_PERCENTAGE))
        )
        groups = groups[drop:]
        if not groups:
            break
        retries += 1
        try:
            return await summarize_once(in_, [m for group in groups for m in group])
        except PromptTooLongError as exc:
            last_err = exc
    raise last_err


async def run_summary(in_: ManageInput) -> list[Message]:
    old_msgs = in_.conv.messages()
    snapshot = in_.recovery.snapshot()
    try:
        text = await summarize_once(in_, old_msgs)
    except PromptTooLongError as exc:
        text = await ptl_retry(in_, old_msgs, exc)
    recovery = build_recovery_attachment(snapshot, in_.tool_defs)
    combined = Message("user", "## 历史会话摘要\n" + text + "\n\n" + recovery)
    return _join_after_summary(combined, pick_recent_tail(old_msgs))


async def force_compact(in_: ManageInput) -> tuple[list[Message], int, int]:
    messages = await run_summary(in_)
    return messages, in_.estimated_token, estimate_tokens(0, messages, 0)


async def auto_compact(in_: ManageInput) -> tuple[list[Message], int, int]:
    try:
        result = await force_compact(in_)
    except Exception:
        in_.auto_tracking.record_failure()
        raise
    in_.auto_tracking.record_success()
    return result
