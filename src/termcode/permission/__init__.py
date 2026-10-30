"""协议无关的权限引擎及其对外接口。"""

from termcode.permission.engine import Engine, new_engine
from termcode.permission.persist import persist_local_allow
from termcode.permission.types import (
    ApprovalError,
    Category,
    Decision,
    Mode,
    Outcome,
    parse_mode,
)

__all__ = [
    "ApprovalError",
    "Category",
    "Decision",
    "Engine",
    "Mode",
    "Outcome",
    "new_engine",
    "parse_mode",
    "persist_local_allow",
]
