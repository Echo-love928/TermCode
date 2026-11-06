"""限制文件边界、原子替换笔记和重新生成索引。"""

import os
import re
import tempfile
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import yaml

from .types import NoteType, UpdateAction


class Store:
    def __init__(self, directory: str) -> None:
        self.directory = Path(directory).resolve()
        self._lock = threading.RLock()

    def ensure_dir(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    def load_index(self) -> str:
        with self._lock:
            try:
                return (self.directory / "MEMORY.md").read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                return ""

    def _path(self, filename: str) -> Path:
        if not re.fullmatch(
            r"(?:" + "|".join(NoteType) + r")_[a-z0-9_]+\.md", filename
        ):
            raise ValueError("非法笔记文件名")
        path = self.directory / filename
        if not path.resolve().is_relative_to(self.directory) or path.is_symlink():
            raise ValueError("笔记路径越界")
        return path

    def _read(self, path: Path) -> tuple[dict, str]:
        text = path.read_text(encoding="utf-8")
        _, header, content = text.split("---", 2)
        meta = yaml.safe_load(header)
        if not isinstance(meta, dict):
            raise ValueError("笔记元数据无效")
        return meta, content.strip()

    def _write(self, path: Path, content: str) -> None:
        if path.is_symlink():
            raise ValueError("拒绝写入符号链接")
        fd, temp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def _index(self) -> None:
        lines = []
        for path in sorted(self.directory.glob("*.md")):
            if path.name == "MEMORY.md":
                continue
            self._path(path.name)
            meta, content = self._read(path)
            title = str(meta["title"]).replace("\n", " ")
            lines.append(
                f"- [{meta['type']}] {title} — "
                f"{' '.join(content.split())[:160]} ({path.name})"
            )
        text = "\n".join(lines) + ("\n" if lines else "")
        if len(lines) > 200 or len(text.encode()) > 25 * 1024:
            raise ValueError("索引超过 200 行或 25KB，请合并或淘汰笔记")
        self._write(self.directory / "MEMORY.md", text)

    def apply(self, actions: list[UpdateAction | dict]) -> None:
        with self._lock:
            self.ensure_dir()
            before = {
                p: p.read_bytes()
                for p in self.directory.glob("*.md")
                if not p.is_symlink()
            }
            try:
                self._apply(actions)
            except Exception:
                # 单批次失败时恢复原笔记和索引，避免遗留未索引的新文件。
                for path in self.directory.glob("*.md"):
                    if path not in before and not path.is_symlink():
                        path.unlink()
                for path, data in before.items():
                    self._write(path, data.decode("utf-8"))
                raise

    def _apply(self, actions: list[UpdateAction | dict]) -> None:
        with self._lock:
            for action in actions:
                a = asdict(action) if isinstance(action, UpdateAction) else action
                kind = a["action"]
                if kind == "create":
                    note_type = NoteType(a["type"])
                    path = self._path(f"{note_type}_{a['slug']}.md")
                    if path.exists():
                        raise ValueError("笔记已存在，请使用 update")
                    meta = {
                        "type": str(note_type),
                        "created": datetime.now().astimezone().isoformat(),
                    }
                else:
                    path = self._path(a["filename"])
                    if kind == "delete":
                        path.unlink(missing_ok=True)
                        continue
                    if kind != "update":
                        raise ValueError("未知记忆操作")
                    meta, _ = self._read(path)
                if not isinstance(a["title"], str) or not isinstance(a["content"], str):
                    raise ValueError("笔记内容类型错误")
                meta.update(
                    title=a["title"], updated=datetime.now().astimezone().isoformat()
                )
                self._write(
                    path,
                    "---\n"
                    + yaml.safe_dump(meta, allow_unicode=True)
                    + "---\n"
                    + a["content"]
                    + "\n",
                )
            self._index()
