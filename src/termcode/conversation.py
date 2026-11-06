"""仅保存在进程内的单会话历史。"""

import copy
import logging
import threading
from collections.abc import Callable

from termcode.llm import Message, ToolCall, ToolResult


class Conversation:
    def __init__(
        self,
        on_append: Callable[[Message], None] | None = None,
        on_replace: Callable[[list[Message]], None] | None = None,
    ) -> None:
        self._messages: list[Message] = []
        self._lock = threading.RLock()
        self._on_append = on_append
        self._on_replace = on_replace

    @classmethod
    def from_messages(cls, msgs: list[Message], on_append=None, on_replace=None):
        conv = cls(on_append, on_replace)
        conv._messages = copy.deepcopy(msgs)
        return conv

    def _notify(self, callback, value) -> None:
        if callback is not None:
            try:
                callback(copy.deepcopy(value))
            except Exception:
                logging.getLogger(__name__).exception("会话存档失败")

    def _append(self, msg: Message) -> None:
        with self._lock:
            self._messages.append(msg)
            # 回调与历史提交使用同一把锁，保证多线程存档顺序一致。
            self._notify(self._on_append, msg)

    def add_user(self, text: str) -> None:
        self._append(Message("user", text))

    def add_assistant(self, text: str) -> None:
        self._append(Message("assistant", text))

    def add_assistant_with_tool_calls(self, text: str, calls: list[ToolCall]) -> None:
        self._append(Message("assistant", text, tool_calls=copy.deepcopy(calls)))

    def add_tool_results(self, results: list[ToolResult]) -> None:
        self._append(Message("tool", tool_results=copy.deepcopy(results)))

    def messages(self) -> list[Message]:
        with self._lock:
            return copy.deepcopy(self._messages)

    def replace_history(self, msgs: list[Message] | None) -> None:
        """深拷贝后整体提交历史，不向外暴露内部列表。"""
        with self._lock:
            self._messages = copy.deepcopy(msgs or [])
            self._notify(self._on_replace, self._messages)

    replace_messages = replace_history

    def length(self) -> int:
        with self._lock:
            return len(self._messages)

    def last_role(self) -> str:
        """返回末条消息角色，空历史返回空字符串。"""
        with self._lock:
            return self._messages[-1].role if self._messages else ""

    def discard_pending_user(self) -> None:
        """失败的请求不遗留未配对的用户消息，保证下一轮仍可正常对话。"""
        with self._lock:
            if self._messages and self._messages[-1].role == "user":
                self._messages.pop()
                self._notify(self._on_replace, self._messages)
