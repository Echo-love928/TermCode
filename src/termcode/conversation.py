"""仅保存在进程内的单会话历史。"""

from termcode.llm import Message, ToolCall, ToolResult


class Conversation:
    def __init__(self) -> None:
        self._messages: list[Message] = []

    def add_user(self, text: str) -> None:
        self._messages.append(Message("user", text))

    def add_assistant(self, text: str) -> None:
        self._messages.append(Message("assistant", text))

    def add_assistant_with_tool_calls(self, text: str, calls: list[ToolCall]) -> None:
        self._messages.append(Message("assistant", text, tool_calls=list(calls)))

    def add_tool_results(self, results: list[ToolResult]) -> None:
        self._messages.append(Message("tool", tool_results=list(results)))

    def messages(self) -> list[Message]:
        return list(self._messages)

    def last_role(self) -> str:
        """返回末条消息角色，空历史返回空字符串。"""
        return self._messages[-1].role if self._messages else ""

    def discard_pending_user(self) -> None:
        """失败的请求不遗留未配对的用户消息，保证下一轮仍可正常对话。"""
        if self._messages and self._messages[-1].role == "user":
            self._messages.pop()
