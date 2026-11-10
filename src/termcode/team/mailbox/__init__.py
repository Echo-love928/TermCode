"""按收件人持久化消息。索引保持稳定，读写可并发追加。"""

from contextlib import suppress
from dataclasses import replace
from pathlib import Path

from ..persistence import atomic_json, file_lock, read_json
from ..types import sanitize
from .message import Message, MessageType

__all__ = ["Box", "Message", "MessageType"]


class Box:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, agent_id: str) -> Path:
        if sanitize(agent_id) != agent_id:
            raise ValueError("无效的邮箱 ID")
        return self.directory / f"{agent_id}.json"

    def _read(self, path: Path) -> list[Message]:
        try:
            return [Message.from_dict(item) for item in read_json(path)]
        except FileNotFoundError:
            return []

    async def read(self, agent_id: str) -> list[Message]:
        return self._read(self._path(agent_id))

    async def read_unread(self, agent_id: str) -> tuple[list[int], list[Message]]:
        unread = [
            (i, msg) for i, msg in enumerate(await self.read(agent_id)) if not msg.read
        ]
        return [i for i, _ in unread], [msg for _, msg in unread]

    def _write(self, path: Path, messages: list[Message]) -> None:
        atomic_json(path, [message.to_dict() for message in messages])

    async def write(self, agent_id: str, msg: Message) -> None:
        path = self._path(agent_id)
        async with file_lock(path.with_suffix(".lock")):
            self._write(path, [*self._read(path), msg])

    async def mark_read(self, agent_id: str, indices: list[int]) -> None:
        if not indices:
            return
        path = self._path(agent_id)
        async with file_lock(path.with_suffix(".lock")):
            marked = set(indices)
            self._write(
                path,
                [
                    replace(msg, read=True) if i in marked else msg
                    for i, msg in enumerate(self._read(path))
                ],
            )

    async def remove(self, agent_id: str) -> None:
        path = self._path(agent_id)
        with suppress(FileNotFoundError):
            async with file_lock(path.with_suffix(".lock")):
                path.unlink(missing_ok=True)
