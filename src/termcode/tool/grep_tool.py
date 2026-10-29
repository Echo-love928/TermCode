"""正则搜索文本，限制单行读取和命中数量。"""

import asyncio
import re
from pathlib import Path
from typing import Any

from termcode.tool import TRUNCATED, Result, _truncate, arguments, string_schema


class GrepTool:
    def name(self) -> str:
        return "grep"

    def description(self) -> str:
        return "用 Python 正则搜索文件内容，返回 文件:行号:内容，最多 100 处。"

    def parameters(self) -> dict[str, Any]:
        return string_schema(
            {"pattern": "Python 正则表达式"},
            path="文件或目录，默认 .",
            glob="文件 glob 过滤",
        )

    async def execute(self, args: str) -> Result:
        try:
            data = arguments(args, self.parameters())
            rx = re.compile(data["pattern"])
            root = Path(data.get("path", "."))
            if not await asyncio.to_thread(root.exists):
                return Result(f"路径不存在: {root}", is_error=True)
            if await asyncio.to_thread(root.is_file):
                files = iter([root])
            else:
                files = root.rglob(data.get("glob") or "*")  # noqa: ASYNC240
            matches: list[str] = []
            while (file := await asyncio.to_thread(next, files, None)) is not None:
                await asyncio.sleep(0)
                if not await asyncio.to_thread(file.is_file):
                    continue
                try:
                    with file.open(encoding="utf-8", errors="replace") as source:
                        lineno = 0
                        while line := source.readline(65537):
                            lineno += 1
                            if len(line) > 65536:
                                return Result(
                                    _truncate("\n".join(matches))
                                    + TRUNCATED
                                    + f"\n{file}:{lineno}:该行过长，未完整搜索"
                                )
                            if rx.search(line):
                                matches.append(f"{file}:{lineno}:{line.rstrip()}")
                                if len(matches) > 100:
                                    return Result(
                                        _truncate("\n".join(matches[:100])) + TRUNCATED
                                    )
                            if lineno % 100 == 0:
                                await asyncio.sleep(0)
                except OSError:
                    continue
            return Result(_truncate("\n".join(matches)) if matches else "无命中")
        except Exception as exc:
            return Result(f"搜索失败: {exc}", is_error=True)
