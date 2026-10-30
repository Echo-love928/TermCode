"""权限判定及人在回路共用的基础类型。"""

from enum import IntEnum


class Mode(IntEnum):
    DEFAULT = 0
    ACCEPT_EDITS = 1
    PLAN = 2
    BYPASS = 3

    def __str__(self) -> str:
        return ("default", "acceptEdits", "plan", "bypassPermissions")[self.value]


def parse_mode(value: str) -> tuple[Mode, bool]:
    for mode in Mode:
        if isinstance(value, str) and str(mode).lower() == value.lower():
            return mode, True
    return Mode.DEFAULT, False


class Decision(IntEnum):
    ALLOW = 0
    DENY = 1
    ASK = 2


class Category(IntEnum):
    READ = 0
    WRITE = 1
    EXEC = 2


class Outcome(IntEnum):
    DENY_ONCE = 0
    ALLOW_ONCE = 1
    ALLOW_FOREVER = 2


class ApprovalError(Exception):
    """无法安全生成或保存永久授权规则。"""
