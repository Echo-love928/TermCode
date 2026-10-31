"""上下文管理唯一入口：静默落盘、自动摘要与强制摘要。"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from termcode.conversation import Conversation
from termcode.llm import Provider, ToolDefinition

from .const import AUTO_SAFETY_MARGIN, SUMMARY_RESERVE
from .layer1 import offload_and_snip
from .layer2 import auto_compact, force_compact
from .state import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    SessionContext,
)
from .token import estimate_tokens


class TriggerKind(Enum):
    AUTO = "auto"
    MANUAL = "manual"
    EMERGENCY = "emergency"


@dataclass
class ManageInput:
    conv: Conversation
    provider: Provider
    model: str
    context_window: int
    tool_defs: list[ToolDefinition]
    replacement: ContentReplacementState
    recovery: RecoveryState
    auto_tracking: CompactCircuitBreaker
    session: SessionContext
    usage_anchor: int
    anchor_msg_len: int
    estimated_token: int
    trigger: TriggerKind
    on_start: Callable[[], None] | None = None


@dataclass(frozen=True)
class ManageOutput:
    before_tokens: int
    after_tokens: int
    summarized: bool = False


class CompactError(Exception):
    """隔离摘要外部异常，交由调用方显示或执行熔断降级。"""


async def manage_context(in_: ManageInput) -> ManageOutput:
    logger = logging.getLogger(__name__)
    count = in_.replacement.replacement_count()
    est = in_.estimated_token
    if in_.trigger != TriggerKind.MANUAL:
        messages = await asyncio.to_thread(
            offload_and_snip, in_.conv.messages(), in_.replacement, in_.session
        )
        in_.conv.replace_history(messages)
        est = estimate_tokens(in_.usage_anchor, messages, in_.anchor_msg_len)
    if in_.trigger == TriggerKind.AUTO:
        threshold = in_.context_window - SUMMARY_RESERVE - AUTO_SAFETY_MARGIN
        if threshold <= 0:
            logger.warning("上下文窗口过小，跳过自动摘要")
        if threshold <= 0 or est < threshold or in_.auto_tracking.tripped():
            replaced = in_.replacement.replacement_count() - count
            if replaced:
                logger.info(
                    "上下文落盘 trigger=%s before=%d after=%d replaced=%d",
                    in_.trigger.value,
                    in_.estimated_token,
                    est,
                    replaced,
                )
            return ManageOutput(in_.estimated_token, est)
    if in_.on_start is not None:
        in_.on_start()
        # 先让消费者显示等待提示，再进入摘要请求。
        await asyncio.sleep(0)
    try:
        fn = auto_compact if in_.trigger == TriggerKind.AUTO else force_compact
        new_messages, before, after = await fn(in_)
        in_.conv.replace_history(new_messages)
    except Exception as exc:
        logger.info(
            "上下文压缩失败 trigger=%s before=%d after=%d replaced=%d",
            in_.trigger.value,
            in_.estimated_token,
            est,
            in_.replacement.replacement_count() - count,
        )
        raise CompactError(str(exc)) from exc
    logger.info(
        "上下文压缩 trigger=%s before=%d after=%d replaced=%d",
        in_.trigger.value,
        before,
        after,
        in_.replacement.replacement_count() - count,
    )
    return ManageOutput(before, after, summarized=True)
