"""会话上下文管理的窄接口。"""

from .compact import (
    CompactError,
    ManageInput,
    ManageOutput,
    TriggerKind,
    manage_context,
)
from .state import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    SessionContext,
    new_session_context,
)

__all__ = [
    "CompactError",
    "ManageInput",
    "ManageOutput",
    "TriggerKind",
    "manage_context",
    "CompactCircuitBreaker",
    "ContentReplacementState",
    "RecoveryState",
    "SessionContext",
    "new_session_context",
]
