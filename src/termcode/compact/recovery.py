"""用快照构造文件、工具和原文边界三段恢复信息。"""

import json

from termcode.llm import ToolDefinition

from .const import (
    ESTIMATE_CHARS_PER_TOKEN,
    RECOVERY_FILE_LIMIT,
    RECOVERY_TOKENS_PER_FILE,
)
from .state import FileReadRecord

BOUNDARY_NOTICE = (
    "需要文件原文、错误原文、用户原话时，请使用文件读取工具重新读取对应路径，"
    "不要依据摘要内容做猜测。工具结果的完整内容保存在预览所标记的路径。"
)


def render_file_block(rec: FileReadRecord) -> str:
    char_limit = int(RECOVERY_TOKENS_PER_FILE * ESTIMATE_CHARS_PER_TOKEN)
    content = rec.content[:char_limit]
    if len(rec.content) > char_limit:
        content += "\n(content truncated)"
    return f"### {rec.path}\n[read at] {rec.timestamp.isoformat()}\n{content}\n"


def render_tools_block(defs: list[ToolDefinition]) -> str:
    return "\n".join(
        f"- {tool.name}: {tool.description}\n  input_schema: "
        + json.dumps(tool.input_schema, separators=(",", ":"), ensure_ascii=False)
        for tool in defs
    )


def build_recovery_attachment(
    snapshot: list[FileReadRecord], tool_defs: list[ToolDefinition]
) -> str:
    files = "\n".join(render_file_block(rec) for rec in snapshot[:RECOVERY_FILE_LIMIT])
    return (
        "## 最近读过的文件\n"
        + (files or "(无)")
        + "\n\n## 当前可用工具\n"
        + render_tools_block(tool_defs)
        + "\n\n## 边界提示\n"
        + BOUNDARY_NOTICE
    )
