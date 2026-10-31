"""自动与紧急压缩的生命周期事件。"""

from dataclasses import dataclass
from enum import Enum


class CompactPhase(Enum):
    BEFORE_AUTO = "before_auto"
    AFTER_AUTO = "after_auto"
    BEFORE_EMERGENCY = "before_emergency"
    AFTER_EMERGENCY = "after_emergency"


@dataclass(frozen=True)
class CompactEvent:
    phase: CompactPhase
    before: int = 0
    after: int = 0
    err: Exception | None = None
