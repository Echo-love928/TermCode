"""验证用量锚点不重复计算历史，中文按 UTF-8 字节计量。"""

import math

from termcode.compact.token import estimate_tokens, message_chars, usage_anchor
from termcode.llm import Message, ToolCall, ToolResult, Usage


def test_estimate_tokens_anchor():
    assert estimate_tokens(0, [], 0) == 0
    msgs = [Message("user", "older"), Message("assistant", "中" * 350)]
    assert estimate_tokens(1000, msgs, 1) == 1000 + math.ceil(1050 / 3.5)
    assert estimate_tokens(1000, msgs, 20) == 1000
    assert estimate_tokens(0, msgs, -1) == math.ceil(1055 / 3.5)
    assert estimate_tokens(5000, [Message("user", "a" * 350)], 0) == 5100


def test_estimate_tokens_large_no_issue():
    assert (
        estimate_tokens(2_000_000_000, [Message("user", "a" * 350)], 0) == 2_000_000_100
    )


def test_message_chars_counts_tool_arguments_and_results():
    assert (
        message_chars(
            [
                Message(
                    "assistant", "文本", tool_calls=[ToolCall("a", "read", '{"x":1}')]
                ),
                Message("tool", tool_results=[ToolResult("a", "你好")]),
            ]
        )
        == 6 + 7 + 6
    )


def test_usage_anchor_sum():
    assert usage_anchor(Usage(10, 20, 30, 40)) == 100
