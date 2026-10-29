"""有行号和体量上限的文本读取。"""

import asyncio
from pathlib import Path
from typing import Any

from termcode.tool import TRUNCATED, Result, _truncate, arguments, string_schema


class ReadFileTool:
    read_only = True

    def name(self) -> str:
        return "read_file"

    def description(self) -> str:
        return "读取 UTF-8 文本并显示行号，最多 2000 行或 256KB。"

    def parameters(self) -> dict[str, Any]:
        return string_schema({"path": "要读取的文件路径"})

    async def execute(self, args: str) -> Result:
        try:
            data = arguments(args, self.parameters())
            return await asyncio.to_thread(self._read, Path(data["path"]))
        except Exception as exc:
            return Result(f"读取失败: {exc}", is_error=True)

    @staticmethod
    def _read(path: Path) -> Result:
        # 只读取上限加一个字节，长行也不会导致整个文件载入内存。
        with path.open("rb") as source:
            raw = source.read(262145)
        text = raw[:262144].decode("utf-8", errors="replace")
        content = _truncate(
            "".join(f"{n:6d}\t{line}\n" for n, line in enumerate(text.splitlines(), 1))
        )
        if len(raw) > 262144 and not content.endswith(TRUNCATED):
            content += TRUNCATED
        return Result(content)
