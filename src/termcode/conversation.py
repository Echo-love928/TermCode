"""仅保存在进程内的单会话历史。"""

from termcode.llm import Message


class Conversation:
    def __init__(self) -> None:
        self._messages: list[Message] = []

    def add_user(self, text: str) -> None:
        self._messages.append(Message("user", text))

    def add_assistant(self, text: str) -> None:
        self._messages.append(Message("assistant", text))

    def messages(self) -> list[Message]:
        return list(self._messages)

    def discard_pending_user(self) -> None:
        """失败的请求不遗留未配对的用户消息，保证下一轮仍可正常对话。"""
        if self._messages and self._messages[-1].role == "user":
            self._messages.pop()
