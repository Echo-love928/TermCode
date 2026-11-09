from dataclasses import dataclass
from enum import StrEnum


class ExitAction(StrEnum):
    KEEP = "keep"
    REMOVE = "remove"


@dataclass(frozen=True)
class ExitOptions:
    discard_changes: bool = False


@dataclass(frozen=True)
class ExitReport:
    removed: bool
    path: str
    branch: str


@dataclass(frozen=True)
class AutoCleanupReport:
    kept: bool
    path: str = ""
    branch: str = ""


class WorktreeHasChangesError(RuntimeError):
    """Worktree 有未提交修改、新提交，或无法确认是否安全删除。"""
