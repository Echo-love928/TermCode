"""协议无关的流事件与供应商接口。"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from termcode.config import ProviderConfig

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"


@dataclass(frozen=True)
class ToolCall:
    """流式拼接完成的工具调用，保留原始 JSON 参数。"""

    id: str
    name: str
    input: str


@dataclass(frozen=True)
class ToolResult:
    """通过调用 ID 配对的工具结果。"""

    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class ToolDefinition:
    """可转换为不同供应商协议的工具定义。"""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


@dataclass(frozen=True)
class StreamEvent:
    text: str = ""
    done: bool = False
    err: Exception | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


class Provider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    def stream(
        self, msgs: list[Message], tools: list[ToolDefinition] | None = None
    ) -> AsyncGenerator[StreamEvent, None]: ...

    async def aclose(self) -> None: ...


def new_provider(cfg: ProviderConfig) -> Provider:
    if cfg.protocol == "anthropic":
        from termcode.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(cfg)
    if cfg.protocol == "openai":
        from termcode.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(cfg)
    raise ValueError("不支持的供应商协议")
