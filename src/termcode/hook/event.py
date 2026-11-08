"""Hook 生命周期事件。"""

from enum import StrEnum


class Event(StrEnum):
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    SESSION_RESUME = "SessionResume"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    STOP = "Stop"
    PRE_USER_MESSAGE = "PreUserMessage"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"
    NOTIFICATION = "Notification"


def is_blocking(event: Event) -> bool:
    return event in (Event.PRE_TOOL_USE, Event.USER_PROMPT_SUBMIT)
