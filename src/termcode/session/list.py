"""历史会话列表及展示元数据。"""

from dataclasses import dataclass
from pathlib import Path

from termcode.compact.state import parse_session_time

from .load import entries


@dataclass(frozen=True)
class SessionInfo:
    id: str
    dir: str
    title: str
    model: str
    modified_at: float
    size: int


def list_sessions(sessions_dir: str) -> list[SessionInfo]:
    root = Path(sessions_dir)
    result = []
    if not root.is_dir():
        return result
    for directory in root.iterdir():
        try:
            parse_session_time(directory.name)
            if directory.is_symlink() or not directory.is_dir():
                continue
            stat = (directory / "conversation.jsonl").stat()
            title, model = "空会话", ""
            for entry in entries(str(directory)):
                model = model or str(entry.get("model", ""))
                if entry.get("role") == "user" and isinstance(
                    entry.get("content"), str
                ):
                    title = entry["content"].replace("\n", " ")
                    title = title[:49] + "…" if len(title) > 50 else title
                    break
            result.append(
                SessionInfo(
                    directory.name,
                    str(directory),
                    title,
                    model,
                    stat.st_mtime,
                    stat.st_size,
                )
            )
        except (ValueError, OSError):
            continue
    return sorted(result, key=lambda info: info.modified_at, reverse=True)
