"""OpenAI Chat Completions 兼容协议适配器。"""

import asyncio
from collections.abc import AsyncGenerator

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from termcode.config import ProviderConfig
from termcode.llm import Message, StreamEvent
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

    async def stream(self, msgs: list[Message]) -> AsyncGenerator[StreamEvent, None]:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        for message in msgs:
            if message.role == "user":
                messages.append({"role": "user", "content": message.content})
            else:
                messages.append({"role": "assistant", "content": message.content})
        try:
            complete = False
            stream = await self._client.chat.completions.create(
                model=self.model, messages=messages, stream=True
            )
            async with stream:
                async for chunk in stream:
                    # 用量等元数据事件可以不带 choices，不能将其视为文本。
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    if choice.delta.content:
                        yield StreamEvent(text=choice.delta.content)
                    if choice.finish_reason is not None:
                        if choice.finish_reason != "stop":
                            raise RuntimeError("回复未完整结束，或返回了不支持的内容")
                        complete = True
            if not complete:
                raise RuntimeError("响应流提前结束，请重新发送问题")
            yield StreamEvent(done=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            yield StreamEvent(err=exc)
