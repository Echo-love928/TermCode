"""九节模板、稳定序列化和草稿剥离。"""

import pytest

from termcode.compact.summary_prompt import (
    SUMMARY_TITLES,
    build_summary_prompt,
    extract_summary,
    serialize_conversation,
)
from termcode.llm import Message, ToolCall, ToolResult


def test_build_summary_prompt_shape():
    prompt = build_summary_prompt([Message("user", "原始需求")])
    assert len(prompt) == 1 and prompt[0].role == "user"
    assert all(title in prompt[0].content for title in SUMMARY_TITLES)
    assert all(
        text in prompt[0].content
        for text in ("<analysis>", "<summary>", "不要调用任何工具", "原始需求")
    )


def test_serialize_conversation_deterministic():
    msgs = [
        Message("user", "问题"),
        Message("assistant", tool_calls=[ToolCall("a", "read", "{}")]),
        Message("tool", tool_results=[ToolResult("a", "结果")]),
    ]
    text = serialize_conversation(msgs)
    assert serialize_conversation(msgs) == text
    assert "[call read id=a args={}]" in text
    assert "[result id=a is_error=False] 结果" in text


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("abc<analysis>草稿</analysis><summary>正文</summary>end", "正文"),
        ("无标签", "无标签"),
        ("<summary>旧</summary><summary>新</summary>", "新"),
        ("<summary>外<summary>内</summary></summary>", "内"),
        ("<analysis>草稿</analysis>正文", "正文"),
    ],
)
def test_extract_summary(raw, expected):
    assert extract_summary(raw) == expected
