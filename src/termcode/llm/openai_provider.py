"""OpenAI Chat Completions 兼容协议适配器。"""

import asyncio
from collections.abc import AsyncGenerator

from openai import AsyncOpenAI, omit
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolUnionParam

from termcode.config import ProviderConfig
from termcode.llm import Message, StreamEvent, ToolCall, ToolDefinition, Usage
from termcode.prompt import SYSTEM_PROMPT


class OpenAIProvider:
    def __init__(self, cfg: ProviderConfig) -> None:
        self._cfg = cfg
        self._client = AsyncOpenAI(
            api_key=cfg.api_key,
            base_url=cfg.base_url or "https://api.openai.com/v1",
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
        self,
        msgs: list[Message],
        tools: list[ToolDefinition] | None = None,
        system_suffix: str = "",
    ) -> AsyncGenerator[StreamEvent, None]:
        system = SYSTEM_PROMPT + ("\n\n" + system_suffix if system_suffix else "")
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system}
        ]
        for message in msgs:
            if message.role == "user":
                messages.append({"role": "user", "content": message.content})
            elif message.role == "tool":
                for result in message.tool_results:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": result.tool_call_id,
                            "content": result.content,
                        }
                    )
            elif message.tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.content or None,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.name,
                                    "arguments": call.input or "{}",
                                },
                            }
                            for call in message.tool_calls
                        ],
                    }
                )
            else:
                messages.append({"role": "assistant", "content": message.content})
        try:
            complete = False
            buffers: dict[int, dict[str, str]] = {}
            definitions: list[ChatCompletionToolUnionParam] = [
                {
                    "type": "function",
                    "function": {
                        "name": d.name,
                        "description": d.description,
                        "parameters": d.input_schema,
                    },
                }
                for d in tools or []
            ]
            stream = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
                tools=definitions if definitions else omit,
            )
            async with stream:
                async for chunk in stream:
                    if chunk.usage is not None:
                        yield StreamEvent(
                            usage=Usage(
                                chunk.usage.prompt_tokens, chunk.usage.completion_tokens
                            )
                        )
                    # 用量元数据可以不带 choices，不能将其视为文本。
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    if choice.delta.content:
                        yield StreamEvent(text=choice.delta.content)
                    for delta in choice.delta.tool_calls or []:
                        buf = buffers.setdefault(
                            delta.index, {"id": "", "name": "", "args": ""}
                        )
                        if delta.id:
                            buf["id"] += delta.id
                        if delta.function:
                            buf["name"] += delta.function.name or ""
                            buf["args"] += delta.function.arguments or ""
                    if choice.finish_reason is not None:
                        if choice.finish_reason not in {"stop", "tool_calls"}:
                            raise RuntimeError("回复未完整结束，或返回了不支持的内容")
                        complete = True
            if not complete:
                raise RuntimeError("响应流提前结束，请重新发送问题")
            if buffers:
                if any(not b["id"] or not b["name"] for b in buffers.values()):
                    raise RuntimeError("工具调用缺少 ID 或名称，请重新发送问题")
                yield StreamEvent(
                    tool_calls=[
                        ToolCall(b["id"], b["name"], b["args"] or "{}")
                        for _, b in sorted(buffers.items())
                    ]
                )
            yield StreamEvent(done=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            yield StreamEvent(err=exc)
