"""协议无关的流事件与供应商接口。"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Literal, Protocol

from termcode.config import ProviderConfig


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class StreamEvent:
    text: str = ""
    done: bool = False
    err: Exception | None = None


class Provider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    def stream(self, msgs: list[Message]) -> AsyncGenerator[StreamEvent, None]: ...

    async def aclose(self) -> None: ...


def new_provider(cfg: ProviderConfig) -> Provider:
    if cfg.protocol == "anthropic":
        from termcode.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(cfg)
    if cfg.protocol == "openai":
        from termcode.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(cfg)
    raise ValueError("不支持的供应商协议")
