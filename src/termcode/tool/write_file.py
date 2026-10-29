"""创建父目录并覆盖写入文本。"""

import asyncio
from pathlib import Path
from typing import Any

from termcode.tool import Result, arguments, string_schema


class WriteFileTool:
    def name(self) -> str:
        return "write_file"

    def description(self) -> str:
        return "以 UTF-8 创建或覆盖文件；自动创建父目录。"

    def parameters(self) -> dict[str, Any]:
        return string_schema({"path": "目标文件路径", "content": "完整文件内容"})

    async def execute(self, args: str) -> Result:
        try:
            data = arguments(args, self.parameters())
            await asyncio.to_thread(self._write, Path(data["path"]), data["content"])
            size = len(data["content"].encode("utf-8"))
            return Result(f"已写入 {data['path']}（{size} 字节）")
        except Exception as exc:
            return Result(f"写入失败: {exc}", is_error=True)

    @staticmethod
    def _write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
