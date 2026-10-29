"""按 glob 模式寻找文件并限制结果数量。"""

import asyncio
from bisect import insort
from pathlib import Path
from typing import Any

from termcode.tool import TRUNCATED, Result, _truncate, arguments, string_schema


class GlobTool:
    read_only = True

    def name(self) -> str:
        return "glob"

    def description(self) -> str:
        return "按 glob 模式查找文件（支持 **），最多返回 100 个路径。"

    def parameters(self) -> dict[str, Any]:
        return string_schema(
            {"pattern": "glob 模式，例如 **/*.py"}, path="根目录，默认 ."
        )

    async def execute(self, args: str) -> Result:
        try:
            data = arguments(args, self.parameters())
            root = Path(data.get("path", "."))
            if not await asyncio.to_thread(root.is_dir):
                return Result(f"目录不存在或不可读: {root}", is_error=True)
            paths: list[str] = []
            count = 0
            iterator = root.glob(data["pattern"])  # noqa: ASYNC240
            index = 0
            while (path := await asyncio.to_thread(next, iterator, None)) is not None:
                index += 1
                if index % 100 == 0:
                    await asyncio.sleep(0)
                if await asyncio.to_thread(path.is_file):
                    count += 1
                    # 有界有序集合保留全局字典序前 100 条，不保存全部路径。
                    insort(paths, path.relative_to(root).as_posix())
                    if len(paths) > 100:
                        paths.pop()
            content = "\n".join(paths) or "无匹配"
            if count > 100:
                content += TRUNCATED
            return Result(_truncate(content))
        except Exception as exc:
            return Result(f"查找失败: {exc}", is_error=True)
