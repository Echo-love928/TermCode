"""仅在原文唯一匹配时修改文件。"""

import asyncio
from pathlib import Path
from typing import Any

from termcode.tool import Result, arguments, string_schema


class EditFileTool:
    def name(self) -> str:
        return "edit_file"

    def description(self) -> str:
        return "将文件中唯一匹配的 old_string 替换为 new_string，匹配数须为 1。"

    def parameters(self) -> dict[str, Any]:
        return string_schema(
            {
                "path": "要修改的文件路径",
                "old_string": "须唯一匹配且非空的原文",
                "new_string": "替换后的文本，可为空",
            }
        )

    async def execute(self, args: str) -> Result:
        try:
            data = arguments(args, self.parameters())
            return await asyncio.to_thread(self._edit, data)
        except Exception as exc:
            return Result(f"修改失败: {exc}", is_error=True)

    @staticmethod
    def _edit(data: dict[str, str]) -> Result:
        path = Path(data["path"])
        # 保留原始换行符，避免修改不相关的 CRLF 内容。
        with path.open(encoding="utf-8", newline="") as source:
            content = source.read()
        count = content.count(data["old_string"])
        if count != 1:
            reason = (
                "未找到匹配的内容" if count == 0 else "原文不唯一，请提供更长上下文"
            )
            return Result(f"匹配到 {count} 处，{reason}", is_error=True)
        path.write_text(
            content.replace(data["old_string"], data["new_string"], 1),
            encoding="utf-8",
            newline="",
        )
        return Result(f"已修改 {path}（替换 1 处）")
