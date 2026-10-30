"""将远端 MCP 工具适配到现有的字符串参数工具协议。"""

import asyncio
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Protocol

import mcp.types as mtypes

from termcode.tool import Result

call_timeout: float = 30.0
_VALID_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


class CallerSession(Protocol):
    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> mtypes.CallToolResult: ...


@dataclass
class McpTool:
    full_name: str
    remote_name: str
    _description: str
    _parameters: dict[str, Any]
    read_only: bool
    caller: CallerSession
    _secrets: tuple[str, ...] = field(default=(), repr=False)
    _warned_non_text: bool = False

    def name(self) -> str:
        return self.full_name

    def description(self) -> str:
        return self._description

    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, args: str) -> Result:
        try:
            arguments = json.loads(args or "{}")
            if not isinstance(arguments, dict):
                return Result("MCP 工具参数必须是 JSON 对象", is_error=True)
        except (ValueError, TypeError):
            return Result("MCP 工具参数不是合法 JSON 对象", is_error=True)
        try:
            result = await asyncio.wait_for(
                self.caller.call_tool(self.remote_name, arguments or None),
                timeout=call_timeout,
            )
        except TimeoutError:
            return Result(f"MCP 工具调用超时 ({call_timeout:g}s)", is_error=True)
        except Exception as exc:
            reason = str(exc)
            for secret in self._secrets:
                if secret:
                    reason = reason.replace(secret, "[redacted]")
            return Result(f"MCP 工具调用失败: {reason}", is_error=True)
        texts = []
        for block in result.content:
            if isinstance(block, mtypes.TextContent):
                texts.append(block.text)
            elif not self._warned_non_text:
                self._warned_non_text = True
                print(
                    f"[mcp] warn: tool {self.full_name} returned "
                    "non-text content blocks (dropped)",
                    file=sys.stderr,
                )
        return Result("\n".join(texts), is_error=bool(result.is_error))


def adapt_tool(
    server_name: str,
    tool: mtypes.Tool,
    client: CallerSession,
    *,
    secrets: tuple[str, ...] = (),
) -> McpTool | None:
    full_name = f"mcp__{server_name}__{tool.name}"
    if not _VALID_NAME.fullmatch(full_name):
        print(
            f"[mcp] warn: skip tool {full_name!r}: name contains illegal characters",
            file=sys.stderr,
        )
        return None
    return McpTool(
        full_name,
        tool.name,
        tool.description or f"来自 MCP server {server_name} 的工具 {tool.name}",
        dict(tool.input_schema or {"type": "object"}),
        tool.annotations is not None and tool.annotations.read_only_hint is True,
        client,
        secrets,
    )
