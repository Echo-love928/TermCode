"""OpenAI Chat Completions 兼容协议适配器。"""

import asyncio
from collections.abc import AsyncGenerator

from openai import AsyncOpenAI, BadRequestError, omit
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolUnionParam

from termcode.config import ProviderConfig
from termcode.llm import PromptTooLongError, Request, StreamEvent, ToolCall, Usage


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

    async def stream(self, req: Request) -> AsyncGenerator[StreamEvent, None]:
        system = "\n\n".join(
            part for part in (req.system.stable, req.system.environment) if part
        )
        messages: list[ChatCompletionMessageParam] = []
        if system:
            messages.append({"role": "system", "content": system})
        for message in req.messages:
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
        if req.reminder:
            messages.append({"role": "user", "content": req.reminder})
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
                for d in req.tools or []
            ]
            stream = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
                max_completion_tokens=req.max_tokens
                if req.max_tokens is not None
                else omit,
                tools=definitions if definitions else omit,
            )
            async with stream:
                async for chunk in stream:
                    if chunk.usage is not None:
                        yield StreamEvent(
                            usage=Usage(
                                chunk.usage.prompt_tokens,
                                chunk.usage.completion_tokens,
                                cache_read=getattr(
                                    getattr(chunk.usage, "prompt_tokens_details", None),
                                    "cached_tokens",
                                    0,
                                )
                                or 0,
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
            if isinstance(exc, BadRequestError) and (
                exc.code == "context_length_exceeded"
                or (
                    isinstance(exc.body, dict)
                    and exc.body.get("code") == "context_length_exceeded"
                )
            ):
                wrapped = PromptTooLongError("OpenAI 上下文超出窗口")
                wrapped.__cause__ = exc
                yield StreamEvent(err=wrapped)
                return
            yield StreamEvent(err=exc)
