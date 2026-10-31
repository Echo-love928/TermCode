"""由 TUI 持有、跨多轮 run 保留的会话上下文状态。"""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from termcode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    SessionContext,
    new_session_context,
)


@dataclass
class SessionRuntime:
    replacement: ContentReplacementState = field(
        default_factory=ContentReplacementState
    )
    recovery: RecoveryState = field(default_factory=RecoveryState)
    auto_tracking: CompactCircuitBreaker = field(default_factory=CompactCircuitBreaker)
    session: SessionContext = field(
        default_factory=lambda: new_session_context(str(Path.cwd()))
    )
    context_window: int = 200000
    # 仅在会话锁内由主对话更新，摘要完成后清零。
    usage_anchor: int = 0
    anchor_msg_len: int = 0
    run_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
