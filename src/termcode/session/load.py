"""容错读取与工具调用配对检查。"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from termcode.llm import Message, ToolCall, ToolResult


def entries(session_dir: str) -> Iterator[dict[str, Any]]:
    with (Path(session_dir) / "conversation.jsonl").open("rb") as stream:
        for line in stream:
            try:
                entry = json.loads(line)
                if isinstance(entry, dict):
                    yield entry
            except (ValueError, UnicodeError):
                continue


def _truncate_orphaned_tool_calls(msgs: list[Message]) -> list[Message]:
    for i, msg in enumerate(msgs):
        if msg.tool_calls:
            expected = {call.id for call in msg.tool_calls}
            actual = (
                {r.tool_call_id for r in msgs[i + 1].tool_results}
                if i + 1 < len(msgs)
                else set()
            )
            if i + 1 >= len(msgs) or expected != actual:
                return msgs[:i]
    return msgs


def load_session(session_dir: str) -> list[Message]:
    msgs: list[Message] = []
    for entry in entries(session_dir):
        if entry.get("type") == "compact":
            msgs.clear()
            continue
        try:
            role = entry["role"]
            content = entry.get("content", "")
            if role not in {"user", "assistant", "tool"} or not isinstance(
                content, str
            ):
                continue
            calls = [ToolCall(**v) for v in entry.get("tool_calls", [])]
            results = [ToolResult(**v) for v in entry.get("tool_results", [])]
            if not all(
                isinstance(c.id, str)
                and isinstance(c.name, str)
                and isinstance(c.input, str)
                for c in calls
            ):
                continue
            if not all(
                isinstance(r.tool_call_id, str) and isinstance(r.content, str)
                for r in results
            ):
                continue
            msgs.append(Message(role, content, calls, results))
        except (KeyError, TypeError, ValueError):
            continue
    return _truncate_orphaned_tool_calls(msgs)


def last_timestamp(session_dir: str) -> float:
    stamp = 0.0
    for entry in entries(session_dir):
        value = entry.get("ts")
        if entry.get("role") in {"user", "assistant", "tool"} and isinstance(
            value, (int, float)
        ):
            stamp = value
    return stamp


def load_usage(session_dir: str) -> tuple[int, int]:
    """旧存档没有用量记录时按零恢复。"""
    usage = (0, 0)
    for entry in entries(session_dir):
        if entry.get("type") != "usage":
            continue
        tokens_in, tokens_out = entry.get("input"), entry.get("output")
        if type(tokens_in) is int and type(tokens_out) is int:
            if tokens_in >= 0 and tokens_out >= 0:
                usage = tokens_in, tokens_out
    return usage
