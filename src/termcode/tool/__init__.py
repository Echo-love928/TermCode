"""工具抽象、注册中心与固定的六个内置工具。"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from termcode.llm import ToolDefinition

DEFAULT_TIMEOUT = 30.0
TRUNCATED = "\n[truncated]"


@dataclass(frozen=True)
class Result:
    """执行失败也以结果值返回，避免中断会话。"""

    content: str
    is_error: bool = False


@runtime_checkable
class Tool(Protocol):
    def name(self) -> str: ...
    def description(self) -> str: ...
    def parameters(self) -> dict[str, Any]: ...
    async def execute(self, args: str) -> Result: ...


def _truncate(s: str, max_lines: int = 2000, max_chars: int = 262144) -> str:
    # 先限制字符再拆行，避免为巨量输出创建额外列表。
    prefix = s[:max_chars]
    lines = prefix.splitlines(keepends=True)
    clipped = "".join(lines[:max_lines])
    return clipped + TRUNCATED if len(clipped) < len(s) else s


def arguments(args: str, schema: dict[str, Any]) -> dict[str, str]:
    """验证这六个工具使用的字符串参数子集。"""
    data = json.loads(args or "{}")
    if not isinstance(data, dict):
        raise ValueError("参数必须是 JSON 对象")
    for key in schema["required"]:
        if key not in data:
            raise ValueError(f"缺少必填参数: {key}")
    for key, value in data.items():
        if key in schema["properties"] and not isinstance(value, str):
            raise ValueError(f"参数 {key} 必须是字符串")
    for key in ("path", "command", "old_string"):
        if key in data and not data[key]:
            raise ValueError(f"参数 {key} 不得为空")
    return data


def string_schema(required: dict[str, str], **optional: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            key: {"type": "string", "description": description}
            for key, description in (required | optional).items()
        },
        "required": list(required),
    }


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name() in self._tools:
            raise ValueError(f"工具名重复: {tool.name()}")
        self._tools[tool.name()] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(t.name(), t.description(), t.parameters())
            for t in self._tools.values()
        ]

    async def execute(
        self,
        name: str,
        args: str,
        timeout: float = DEFAULT_TIMEOUT,  # noqa: ASYNC109
    ) -> Result:
        tool = self.get(name)
        if tool is None:
            return Result(f"未知工具: {name}", is_error=True)
        try:
            return await asyncio.wait_for(tool.execute(args or "{}"), timeout)
        except TimeoutError:
            return Result(f"工具 {name} 执行超时（{timeout}s），已终止", is_error=True)
        except Exception as exc:
            return Result(f"工具 {name} 执行失败: {exc}", is_error=True)


def new_default_registry() -> Registry:
    from termcode.tool.bash import BashTool
    from termcode.tool.edit_file import EditFileTool
    from termcode.tool.glob_tool import GlobTool
    from termcode.tool.grep_tool import GrepTool
    from termcode.tool.read_file import ReadFileTool
    from termcode.tool.write_file import WriteFileTool

    registry = Registry()
    for tool in (
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        BashTool(),
        GlobTool(),
        GrepTool(),
    ):
        registry.register(tool)
    return registry
