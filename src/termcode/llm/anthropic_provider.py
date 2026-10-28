"""Anthropic Messages 协议适配器。"""

import asyncio
from collections.abc import AsyncGenerator

from anthropic import AsyncAnthropic, omit
from anthropic.types import MessageParam, ThinkingConfigEnabledParam

from termcode.config import ProviderConfig
from termcode.llm import Message, StreamEvent
from termcode.prompt import SYSTEM_PROMPT


class AnthropicProvider:
    def __init__(self, cfg: ProviderConfig) -> None:
        self._cfg = cfg
        self._client = AsyncAnthropic(
            api_key=cfg.api_key,
            base_url=cfg.base_url or "https://api.anthropic.com",
            max_retries=0,
            timeout=60.0,
        )

    @property
    def name(self) -> str:
        return self._cfg.name

    @property
    def model(self) -> str:
        return self._cfg.model

    async def aclose(self) -> None:
        await self._client.close()

    async def stream(self, msgs: list[Message]) -> AsyncGenerator[StreamEvent, None]:
        messages: list[MessageParam] = []
        for message in msgs:
            if message.role == "user":
                messages.append({"role": "user", "content": message.content})
            else:
                messages.append({"role": "assistant", "content": message.content})
        thinking: ThinkingConfigEnabledParam = {
            "type": "enabled",
            "budget_tokens": 2048,
        }
        try:
            complete = False
            stop_reason: str | None = None
            async with self._client.messages.stream(
                model=self.model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=messages,
                thinking=thinking if self._cfg.thinking else omit,
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            yield StreamEvent(text=event.delta.text)
                    elif event.type == "message_delta":
                        stop_reason = event.delta.stop_reason
                    elif event.type == "message_stop":
                        complete = True
            if not complete:
                raise RuntimeError("响应流提前结束，请重新发送问题")
            if stop_reason not in {"end_turn", "stop_sequence"}:
                raise RuntimeError(
                    "回复未完整结束，可能达到长度限制或返回了不支持的内容"
                )
            yield StreamEvent(done=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            yield StreamEvent(err=exc)
