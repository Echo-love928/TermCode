"""线程安全、追加写并立即刷盘的会话存档。"""

import json
import os
import threading
import time
from dataclasses import asdict
from pathlib import Path

from termcode.llm import Message


class Writer:
    def __init__(self, session_dir: str, model: str = "") -> None:
        self.directory = Path(session_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.model = model
        self._lock = threading.RLock()
        self._file = (self.directory / "conversation.jsonl").open("a+b")
        # 崩溃留下半行时先隔开，避免下一条有效消息与坏行粘连。
        self._file.seek(0, os.SEEK_END)
        self._first = self._file.tell() == 0
        if not self._first:
            self._file.seek(-1, os.SEEK_END)
            if self._file.read(1) != b"\n":
                self._file.write(b"\n")
                self._file.flush()
                os.fsync(self._file.fileno())

    @classmethod
    def open_existing(cls, session_dir: str, model: str = "") -> "Writer":
        if not (Path(session_dir) / "conversation.jsonl").is_file():
            raise FileNotFoundError(session_dir)
        return cls(session_dir, model)

    def _write(self, entry: dict) -> None:
        self._file.write(
            (
                json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
            ).encode()
        )
        self._file.flush()
        os.fsync(self._file.fileno())

    def append(
        self, msg: Message, model: str | None = None, is_first: bool | None = None
    ) -> None:
        with self._lock:
            entry = asdict(msg) | {"ts": int(time.time())}
            if self._first:
                entry["model"] = self.model if model is None else model
            self._write(entry)
            self._first = False

    on_append = append
    append_message = append

    def write_compact_marker(self) -> None:
        with self._lock:
            self._write({"type": "compact", "ts": int(time.time())})

    def append_all(self, msgs: list[Message]) -> None:
        with self._lock:
            for msg in msgs:
                self.append(msg)

    def replace_all(self, msgs: list[Message]) -> None:
        with self._lock:
            self.write_compact_marker()
            self.append_all(msgs)

    on_replace = replace_all

    def close(self) -> None:
        with self._lock:
            self._file.close()

    def __enter__(self) -> "Writer":
        return self

    def __exit__(self, *args) -> None:
        self.close()
