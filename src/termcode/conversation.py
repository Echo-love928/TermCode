"""仅保存在进程内的单会话历史。"""

import copy
import threading

from termcode.llm import Message, ToolCall, ToolResult


class Conversation:
    def __init__(self) -> None:
        self._messages: list[Message] = []
        self._lock = threading.RLock()

    def add_user(self, text: str) -> None:
        with self._lock:
            self._messages.append(Message("user", text))

    def add_assistant(self, text: str) -> None:
        with self._lock:
            self._messages.append(Message("assistant", text))

    def add_assistant_with_tool_calls(self, text: str, calls: list[ToolCall]) -> None:
        with self._lock:
            self._messages.append(
                Message("assistant", text, tool_calls=copy.deepcopy(calls))
            )

    def add_tool_results(self, results: list[ToolResult]) -> None:
        with self._lock:
            self._messages.append(Message("tool", tool_results=copy.deepcopy(results)))

    def messages(self) -> list[Message]:
        with self._lock:
            return copy.deepcopy(self._messages)

    def replace_history(self, msgs: list[Message] | None) -> None:
        """深拷贝后整体提交历史，不向外暴露内部列表。"""
        with self._lock:
            self._messages = copy.deepcopy(msgs or [])

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
