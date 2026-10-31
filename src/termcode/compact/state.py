"""跨轮复用的会话状态与线程安全账本。"""

from __future__ import annotations

import logging
import random
import secrets
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from .const import MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES


class ContentReplacementState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._seen_ids: set[str] = set()
        self._replacements: dict[str, str] = {}

    @contextmanager
    def locked(self) -> Iterator[None]:
        """一条工具消息的聚合判断和逐项决策共用同一临界区。"""
        with self._lock:
            yield

    def has_seen(self, tool_use_id: str) -> bool:
        with self._lock:
            return tool_use_id in self._seen_ids

    def replacement_count(self) -> int:
        with self._lock:
            return len(self._replacements)

    def decide_once(
        self,
        tool_use_id: str,
        original_content: str,
        decide: Callable[[], tuple[str, str]],
    ) -> str:
        """原子完成查账、落盘决策与提交；失败的决策允许下轮重试。"""
        with self._lock:
            if tool_use_id in self._seen_ids:
                return self._replacements.get(tool_use_id, original_content)
            decision, preview = decide()
            if decision == "replaced":
                self._replacements[tool_use_id] = preview
            if decision in {"kept", "replaced"}:
                self._seen_ids.add(tool_use_id)
            return preview if decision == "replaced" else original_content


class CompactCircuitBreaker:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._consecutive_failures = 0

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive_failures

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1

    def tripped(self) -> bool:
        with self._lock:
            return self._consecutive_failures >= MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES


@dataclass
class FileReadRecord:
    path: str
    content: str
    timestamp: datetime


class RecoveryState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._files: dict[str, FileReadRecord] = {}

    def record_file(self, path: str, content: str) -> None:
        absolute = str(Path(path).resolve())
        with self._lock:
            self._files[absolute] = FileReadRecord(absolute, content, datetime.now(UTC))

    def snapshot(self) -> list[FileReadRecord]:
        with self._lock:
            return sorted(
                (replace(rec) for rec in self._files.values()),
                key=lambda rec: rec.timestamp,
                reverse=True,
            )


@dataclass(frozen=True)
class SessionContext:
    session_id: str
    spill_dir: str


def _new_session_id() -> str:
    try:
        suffix = secrets.token_hex(4)
    except Exception:
        logging.getLogger(__name__).warning("会话随机数生成失败，使用本地随机数")
        suffix = random.Random(time.time_ns()).randbytes(4).hex()
    return f"{int(time.time())}-{suffix}"


def new_session_context(workspace: str) -> SessionContext:
    session_id = _new_session_id()
    spill_dir = (
        Path(workspace).resolve() / ".termcode/sessions" / session_id / "tool-results"
    )
    try:
        spill_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 不可写工作区仍允许对话，首次落盘时按原文降级。
        logging.getLogger(__name__).warning("无法创建工具结果目录")
    return SessionContext(session_id, str(spill_dir))
