"""协议无关的流事件与供应商接口。"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from termcode.config import ProviderConfig

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"


class PromptTooLongError(Exception):
    """供应商上报输入超出上下文窗口时的统一异常。"""


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
class Usage:
    """一次模型请求的输入和输出 token 用量。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_write: int = 0
    cache_read: int = 0
    # 数值仍按兼容约定缺省为零，另保留服务端是否提供了有效写入计数。
    cache_write_reported: bool = False


@dataclass(frozen=True)
class System:
    stable: str = ""
    environment: str = ""


@dataclass(frozen=True)
class Request:
    messages: list[Message] = field(default_factory=list)
    tools: list[ToolDefinition] | None = field(default_factory=list)
    system: System = field(default_factory=System)
    reminder: str = ""
    max_tokens: int | None = None


@dataclass(frozen=True)
class StreamEvent:
    text: str = ""
    done: bool = False
    err: Exception | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage | None = None


class Provider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    def stream(self, req: Request) -> AsyncGenerator[StreamEvent, None]:
        """稳定提示、环境与临时提醒分开传入；结束前返回用量（若服务提供）。"""
        ...

    async def aclose(self) -> None: ...


def new_provider(cfg: ProviderConfig) -> Provider:
    if cfg.protocol == "anthropic":
        from termcode.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(cfg)
    if cfg.protocol == "openai":
        from termcode.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(cfg)
    raise ValueError("不支持的供应商协议")
