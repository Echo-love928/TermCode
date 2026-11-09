import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile


@dataclass(frozen=True)
class WorktreeSession:
    original_cwd: str
    worktree_path: str
    worktree_name: str
    original_branch: str
    original_head_commit: str
    session_id: str
    hook_based: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "WorktreeSession":
        session = cls(**json.loads(raw))
        if not all(
            isinstance(value, str)
            for key, value in asdict(session).items()
            if key != "hook_based"
        ) or not isinstance(session.hook_based, bool):
            raise ValueError("Worktree 会话字段类型不正确")
        return session


def load_session(path: Path) -> WorktreeSession | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return WorktreeSession.from_json(raw) if raw and raw != "null" else None


def save_session(path: Path, session: WorktreeSession | None) -> None:
    stream = NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    )
    tmp = Path(stream.name)
    try:
        with stream:
            stream.write(session.to_json() if session else "null")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def clear_session(path: Path) -> None:
    save_session(path, None)
