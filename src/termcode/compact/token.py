"""主对话真实用量锚点与新增消息的字节增量估算。"""

import json
import math

from termcode.llm import Message, Usage

from .const import ESTIMATE_CHARS_PER_TOKEN


def usage_anchor(u: Usage) -> int:
    return u.input_tokens + u.output_tokens + u.cache_read + u.cache_write


def message_chars(msgs: list[Message]) -> int:
    total = 0
    for msg in msgs:
        total += len((msg.content or "").encode("utf-8"))
        for call in msg.tool_calls or []:
            args = call.input
            text = (
                args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
            )
            total += len(text.encode("utf-8"))
        total += sum(
            len((result.content or "").encode("utf-8"))
            for result in msg.tool_results or []
        )
    return total


def estimate_tokens(anchor: int, all_msgs: list[Message], anchor_msg_len: int) -> int:
    tail = all_msgs[max(0, anchor_msg_len) :]
    return anchor + math.ceil(message_chars(tail) / ESTIMATE_CHARS_PER_TOKEN)
