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
from termcode.hook import DispatchResult, Engine, Event
from termcode.hook.rule import Payload
from termcode.permission import Mode


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
    turn_count: int = 0
    # 仅在会话锁内由主对话更新，摘要完成后清零。
    usage_anchor: int = 0
    anchor_msg_len: int = 0
    run_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    active_skills: dict[str, str] = field(default_factory=dict)
    hook_engine: Engine | None = None
    pending_reminders: list[str] = field(default_factory=list)
    hook_once_fired: set[str] = field(default_factory=set, repr=False)

    async def dispatch_hook(
        self, event: Event, cwd: str, mode: Mode, payload: Payload | None = None
    ) -> DispatchResult:
        if self.hook_engine is None:
            return DispatchResult()
        result = await self.hook_engine.dispatch(
            event,
            {
                **(payload or {}),
                "session_id": self.session.session_id,
                "cwd": cwd,
                "mode": mode.name.lower(),
            },
            once_fired=self.hook_once_fired,
        )
        if event is not Event.SESSION_END and not (
            event is Event.USER_PROMPT_SUBMIT and result.blocked
        ):
            self.append_reminders(result.injected_prompts)
        return result

    def append_reminders(self, prompts: list[str]) -> None:
        self.pending_reminders.extend(prompts)

    def take_reminders(self) -> list[str]:
        reminders, self.pending_reminders = self.pending_reminders, []
        return reminders

    def reset_for_new_session(self, ses_ctx: SessionContext) -> None:
        """在空闲状态无 await 地提交新会话，保留上下文窗口与会话锁。"""
        if self.run_lock.locked():
            raise RuntimeError("请等待当前任务完成")
        self.replacement = ContentReplacementState()
        self.recovery = RecoveryState()
        self.auto_tracking = CompactCircuitBreaker()
        self.session = ses_ctx
        self.usage_anchor = self.anchor_msg_len = self.turn_count = 0
        self.active_skills.clear()
        self.pending_reminders.clear()
        self.hook_once_fired = set()
