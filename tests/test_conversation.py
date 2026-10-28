"""会话顺序、隔离及失败回滚。"""

from dataclasses import FrozenInstanceError

import pytest

from termcode.conversation import Conversation


def test_history_and_copy():
    conv = Conversation()
    conv.add_user("记住名字小林")
    conv.add_assistant("好的")
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
