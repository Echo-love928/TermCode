"""超大工具结果的确定性预览与幂等落盘。"""

import copy
import logging
import os
import tempfile
from dataclasses import replace
from pathlib import Path

from termcode.llm import Message

from .const import (
    MESSAGE_AGGREGATE_LIMIT,
    PREVIEW_HEAD_BYTES,
    PREVIEW_HEAD_LINES,
    SINGLE_RESULT_LIMIT,
)
from .state import ContentReplacementState, SessionContext


def _spill_path(session: SessionContext, tool_use_id: str) -> Path:
    if (
        not tool_use_id
        or tool_use_id in {".", ".."}
        or any(char in tool_use_id for char in "/\\:\0")
    ):
        raise OSError("工具调用 ID 不能作为落盘文件名")
    return Path(session.spill_dir) / tool_use_id


def spill_single(session: SessionContext, tool_use_id: str, content: str) -> None:
    path = _spill_path(session, tool_use_id)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # 写完临时文件再原子发布，写入失败不会留下被误认为完整结果的文件。
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
            temp_path = Path(temp.name)
            temp.write(content.encode("utf-8"))
        try:
            os.link(temp_path, path)
        except FileExistsError:
            pass
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _head_preview(content: str) -> str:
    head = "".join(content.splitlines(keepends=True)[:PREVIEW_HEAD_LINES])
    return head.encode("utf-8")[:PREVIEW_HEAD_BYTES].decode("utf-8", errors="ignore")


def build_preview(original_bytes: int, head: str, spill_path: str) -> str:
    return "\n".join(
        [
            f"[content offloaded] original size: {original_bytes} bytes",
            f"[saved to] {spill_path}",
            "[head preview]",
            head,
            "完整内容已保存到上述路径，如需查看请用文件读取工具读取该路径，"
            "不要凭头部预览猜测全文",
        ]
    )


def offload_and_snip(
    msgs: list[Message], state: ContentReplacementState, session: SessionContext
) -> list[Message]:
    out = copy.deepcopy(msgs)
    with state.locked():
        for index, msg in enumerate(out):
            if msg.role != "tool":
                continue
            results = list(msg.tool_results)
            candidates: dict[str, tuple[str, int]] = {}
            for result in results:
                if not state.has_seen(result.tool_call_id):
                    candidates.setdefault(
                        result.tool_call_id,
                        (result.content, len(result.content.encode("utf-8"))),
                    )
            remaining = sum(size for _, size in candidates.values())
            # 排序稳定，同样大小按工具结果的原始顺序决策。
            decisions: dict[str, str] = {}
            for id_, (content, size) in sorted(
                candidates.items(), key=lambda entry: entry[1][1], reverse=True
            ):
                should_spill = (
                    size > SINGLE_RESULT_LIMIT or remaining > MESSAGE_AGGREGATE_LIMIT
                )

                def decide(
                    should_spill: bool = should_spill,
                    id_: str = id_,
                    content: str = content,
                    size: int = size,
                ) -> tuple[str, str]:
                    if not should_spill:
                        return "kept", ""
                    try:
                        path = _spill_path(session, id_)
                        preview = build_preview(size, _head_preview(content), str(path))
                        spill_single(session, id_, content)
                        return "replaced", preview
                    except (OSError, UnicodeError):
                        logging.getLogger(__name__).warning("工具结果落盘失败：%s", id_)
                        return "skip", ""

                decisions[id_] = state.decide_once(id_, content, decide)
                if should_spill and state.has_seen(id_):
                    remaining -= size
            for j, result in enumerate(results):
                resolved = decisions.get(result.tool_call_id)
                if resolved is None:
                    resolved = state.decide_once(
                        result.tool_call_id, result.content, lambda: ("skip", "")
                    )
                results[j] = replace(result, content=resolved)
            out[index] = replace(msg, tool_results=results)
    return out
