"""协议无关的单轮工具调用与续答编排。"""

from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass
from enum import Enum

from termcode.conversation import Conversation
from termcode.llm import Provider, ToolCall, ToolResult
from termcode.tool import Registry


class Phase(Enum):
    START = "start"
    END = "end"


@dataclass(frozen=True)
class ToolEvent:
    name: str
    args: str = ""
    phase: Phase = Phase.START
    result: str = ""
    is_error: bool = False


@dataclass(frozen=True)
class Event:
    text: str = ""
    tool: ToolEvent | None = None
    done: bool = False
    err: Exception | None = None


class Agent:
    def __init__(self, provider: Provider, registry: Registry) -> None:
        self._provider = provider
        self._registry = registry

    async def run(self, conv: Conversation) -> AsyncGenerator[Event, None]:
        definitions = self._registry.definitions()
        try:
            # 最多两次模型请求；仅第一次请求允许执行工具。
            for turn in range(2):
                text = ""
                calls: list[ToolCall] = []
                complete = False
                async with aclosing(
                    self._provider.stream(conv.messages(), definitions)
                ) as stream:
                    async for event in stream:
                        if event.err is not None:
                            yield Event(err=event.err)
                            return
                        if event.text:
                            text += event.text
                            yield Event(text=event.text)
                        calls.extend(event.tool_calls)
                        if event.done:
                            complete = True
                            break
                if not complete:
                    yield Event(err=RuntimeError("响应流未正常结束，请重新发送问题"))
                    return
                if turn == 1 or not calls:
                    if turn == 1 and calls:
                        notice = "\n本轮已达到工具调用上限，请发送新请求继续。"
                        text += notice
                        yield Event(text=notice)
                    conv.add_assistant(text)
                    yield Event(done=True)
                    return
                conv.add_assistant_with_tool_calls(text, calls)
                results = []
                for call in calls:
                    preview = call.input[:80] + ("…" if len(call.input) > 80 else "")
                    yield Event(tool=ToolEvent(call.name, preview))
                    result = await self._registry.execute(call.name, call.input)
                    results.append(ToolResult(call.id, result.content, result.is_error))
                    yield Event(
                        tool=ToolEvent(
                            call.name,
                            preview,
                            Phase.END,
                            result.content,
                            result.is_error,
                        )
                    )
                conv.add_tool_results(results)
        except Exception as exc:
            yield Event(err=exc)
