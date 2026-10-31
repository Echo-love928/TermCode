"""会话顺序、隔离及失败回滚。"""

from dataclasses import FrozenInstanceError

import pytest

from termcode.conversation import Conversation
from termcode.llm import Message, ToolCall, ToolResult


def test_history_and_copy():
    conv = Conversation()
    assert conv.last_role() == ""
    conv.add_user("记住名字小林")
    assert conv.last_role() == "user"
    conv.add_assistant("好的")
    assert conv.last_role() == "assistant"
    conv.add_user("我的名字是什么？")
    messages = conv.messages()
    assert [m.role for m in messages] == ["user", "assistant", "user"]
    assert messages[0].content == "记住名字小林"
    messages.clear()
    assert len(conv.messages()) == 3
    with pytest.raises(FrozenInstanceError):
        conv.messages()[0].content = "修改"


def test_session_isolation_and_failed_turn():
    conv = Conversation()
    conv.add_user("成功的问题")
    conv.add_assistant("答案")
    conv.add_user("失败的问题")
    conv.discard_pending_user()
    conv.discard_pending_user()
    assert len(conv.messages()) == 2
    assert Conversation().messages() == []


def test_conversation_copies_tool_lists():
    conv = Conversation()
    calls = [ToolCall("1", "x", "{}")]
    results = [ToolResult("1", "bad", True)]
    conv.add_user("hi")
    conv.add_assistant_with_tool_calls("", calls)
    conv.add_tool_results(results)
    conv.add_assistant("done")
    calls.clear()
    results.clear()
    assert [m.role for m in conv.messages()] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert conv.messages()[1].tool_calls and conv.messages()[2].tool_results
    assert Message("user", "hello").tool_calls == []


def test_replace_history_deep_copy():
    conv = Conversation()
    original = [Message("assistant", tool_calls=[ToolCall("a", "read", "{}")])]
    conv.replace_history(original)
    original[0].tool_calls.clear()
    original.clear()
    returned = conv.messages()
    returned[0].tool_calls.clear()
    assert conv.length() == 1
    assert conv.messages()[0].tool_calls[0].id == "a"


def test_replace_history_empty():
    conv = Conversation()
    conv.replace_history([Message("user", "hello")])
    conv.replace_history(None)
    assert conv.messages() == []
    conv.replace_history([])
    assert conv.length() == 0
