"""Anthropic Messages 协议适配器。"""

import asyncio
import json
from collections.abc import AsyncGenerator

from anthropic import AsyncAnthropic, omit
from anthropic.types import (
    ContentBlockParam,
    MessageParam,
    ThinkingConfigEnabledParam,
    ToolUnionParam,
)

from termcode.config import ProviderConfig
from termcode.llm import Message, StreamEvent, ToolCall, ToolDefinition
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

    async def stream(
        self, msgs: list[Message], tools: list[ToolDefinition] | None = None
    ) -> AsyncGenerator[StreamEvent, None]:
        messages: list[MessageParam] = []
        for message in msgs:
            if message.role == "user":
                messages.append({"role": "user", "content": message.content})
            elif message.role == "tool":
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": r.tool_call_id,
                                "content": r.content,
                                "is_error": r.is_error,
                            }
                            for r in message.tool_results
                        ],
                    }
                )
            elif message.tool_calls:
                blocks: list[ContentBlockParam] = []
                if message.content:
                    blocks.append({"type": "text", "text": message.content})
                blocks.extend(
                    {
                        "type": "tool_use",
                        "id": c.id,
                        "name": c.name,
                        "input": json.loads(c.input or "{}"),
                    }
                    for c in message.tool_calls
                )
                messages.append({"role": "assistant", "content": blocks})
            else:
                messages.append({"role": "assistant", "content": message.content})
        thinking: ThinkingConfigEnabledParam = {
            "type": "enabled",
            "budget_tokens": 2048,
        }
        try:
            complete = False
            stop_reason: str | None = None
            definitions: list[ToolUnionParam] = [
                {
                    "name": d.name,
                    "description": d.description,
                    "input_schema": d.input_schema,
                }
                for d in tools or []
            ]
            has_tool_history = any(m.tool_calls or m.tool_results for m in msgs)
            # 首轮思考增量沿用 ch02 丢弃策略，含工具历史时不再启用 thinking。
            async with self._client.messages.stream(
                model=self.model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=messages,
                thinking=thinking
                if self._cfg.thinking and not has_tool_history
                else omit,
                tools=definitions if definitions else omit,
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            yield StreamEvent(text=event.delta.text)
                    elif event.type == "message_delta":
                        stop_reason = event.delta.stop_reason
                    elif event.type == "message_stop":
                        complete = True
                final_message = await stream.get_final_message() if complete else None
            if not complete:
                raise RuntimeError("响应流提前结束，请重新发送问题")
            if stop_reason not in {"end_turn", "stop_sequence", "tool_use"}:
                raise RuntimeError(
                    "回复未完整结束，可能达到长度限制或返回了不支持的内容"
                )
            if stop_reason == "tool_use" and final_message is not None:
                calls = [
                    ToolCall(b.id, b.name, json.dumps(b.input, ensure_ascii=False))
                    for b in final_message.content
                    if b.type == "tool_use"
                ]
                if not calls:
                    raise RuntimeError("响应缺少工具调用内容")
                yield StreamEvent(tool_calls=calls)
            yield StreamEvent(done=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            yield StreamEvent(err=exc)
