"""恢复段的文件顺序、截断、稳定文案和完整工具 schema。"""

import json
from datetime import UTC, datetime, timedelta

from termcode.compact.recovery import (
    BOUNDARY_NOTICE,
    build_recovery_attachment,
    render_file_block,
)
from termcode.compact.state import FileReadRecord
from termcode.llm import ToolDefinition


def test_render_file_block_truncate():
    record = FileReadRecord("/x", "a" * 17500 + "不可出现的尾部", datetime.now(UTC))
    block = render_file_block(record)
    assert "a" * 17500 in block
    assert "不可出现" not in block
    assert block.endswith("(content truncated)\n")


def test_build_recovery_attachment_limit():
    records = [
        FileReadRecord(f"/file{i}", str(i), datetime.now(UTC) - timedelta(seconds=i))
        for i in range(7)
    ]
    text = build_recovery_attachment(records, [])
    assert all(f"/file{i}" in text for i in range(5))
    assert "/file5" not in text and "/file6" not in text
    assert [text.index(f"/file{i}") for i in range(5)] == sorted(
        text.index(f"/file{i}") for i in range(5)
    )


def test_build_recovery_attachment_tools_exact_and_boundary_stable():
    defs = [
        ToolDefinition(
            "read",
            "读取",
            {"type": "object", "properties": {"path": {"type": "string"}}},
        ),
        ToolDefinition("edit", "编辑", {}),
    ]
    text = build_recovery_attachment([], defs)
    assert text == build_recovery_attachment([], defs)
    assert BOUNDARY_NOTICE in text
    assert all(
        title in text for title in ("最近读过的文件", "当前可用工具", "边界提示")
    )
    assert [
        line.split(":", 1)[0][2:] for line in text.splitlines() if line.startswith("- ")
    ] == [d.name for d in defs]
    schemas = [
        json.loads(line.split("input_schema: ", 1)[1])
        for line in text.splitlines()
        if "input_schema:" in line
    ]
    assert schemas == [d.input_schema for d in defs]
