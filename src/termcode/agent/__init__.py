"""协议无关的 ReAct 循环编排与保序分批并发。"""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass
from enum import Enum, IntEnum

from termcode import __version__, prompt
from termcode.conversation import Conversation
from termcode.llm import Provider, Request, System, ToolCall, ToolDefinition, ToolResult
from termcode.llm import Usage as LLMUsage
from termcode.tool import Registry

MAX_ITERATIONS = 25
MAX_UNKNOWN_RUN = 3
PLAN_REMINDER_INTERVAL = 4
NOTICE_MAX_ITER = "（已达最大迭代轮数 25，自动停止；可继续发消息推进。）"
NOTICE_UNKNOWN_TOOLS = "（连续多轮只请求到未注册的工具，自动停止。）"
NOTICE_STREAM_ERR = "（请求出错，本轮已中断。）"
NOTICE_CANCELLED = "（已取消。）"
EMPTY_REPLY = "（模型未返回正文）"


class Phase(Enum):
    START = "start"
    END = "end"


class Mode(IntEnum):
    NORMAL = 0
    PLAN = 1


@dataclass(frozen=True)
class Usage:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0
    cache_write_reported: bool = False


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
    usage: Usage | None = None
    iter: int = 0
    notice: str = ""


class _TurnCancelled(Exception):
    """本轮取消，由主循环统一维护历史并收尾。"""


Push = Callable[[Event], None]


async def _until_cancel[T](operation: Awaitable[T], cancel: asyncio.Event) -> T:
    """同时监听取消和阻塞操作，并回收双方的任务。"""
    task = asyncio.ensure_future(operation)
    watcher = asyncio.create_task(cancel.wait())
    try:
        await asyncio.wait((task, watcher), return_when=asyncio.FIRST_COMPLETED)
        if cancel.is_set():
            raise _TurnCancelled
        return await task
    finally:
        task.cancel()
        watcher.cancel()
        await asyncio.gather(task, watcher, return_exceptions=True)


def ensure_assistant_tail(conv: Conversation, fallback: str) -> None:
    if conv.last_role() != "assistant":
        conv.add_assistant(fallback)


def args_preview(call: ToolCall) -> str:
    return call.input[:80] + ("…" if len(call.input) > 80 else "")


class Agent:
    def __init__(
        self, provider: Provider, registry: Registry, version: str = __version__
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._version = version

    async def run(
        self,
        conv: Conversation,
        mode: Mode = Mode.NORMAL,
        cancel: asyncio.Event | None = None,
    ) -> AsyncGenerator[Event, None]:
        # 单个生产者独占历史；消费者提前关闭时仍等待其取消和配对收尾。
        cancel = cancel if cancel is not None else asyncio.Event()
        queue: asyncio.Queue[Event | None] = asyncio.Queue()

        async def produce() -> None:
            try:
                await self._loop(conv, mode, cancel, queue.put_nowait)
            finally:
                queue.put_nowait(None)

        worker = asyncio.create_task(produce())
        try:
            while (event := await queue.get()) is not None:
                yield event
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def _loop(
        self, conv: Conversation, mode: Mode, cancel: asyncio.Event, push: Push
    ) -> None:
        definitions = (
            self._registry.read_only_definitions()
            if mode == Mode.PLAN
            else self._registry.definitions()
        )
        unknown_run = 0
        try:
            if cancel.is_set():
                raise _TurnCancelled
            environment = await _until_cancel(
                asyncio.to_thread(
                    prompt.gather_environment, self._version, self._provider.model
                ),
                cancel,
            )
            system = System(prompt.build_system_prompt(), environment.render())
            for iteration in range(1, MAX_ITERATIONS + 1):
                if cancel.is_set():
                    raise _TurnCancelled
                push(Event(iter=iteration))
                reminder = (
                    prompt.plan_reminder((iteration - 1) % PLAN_REMINDER_INTERVAL == 0)
                    if mode == Mode.PLAN
                    else ""
                )
                text, calls, usage = await self._stream_once(
                    conv, definitions, system, reminder, cancel, push
                )
                if usage is not None:
                    push(
                        Event(
                            usage=Usage(
                                usage.input_tokens,
                                usage.output_tokens,
                                usage.cache_write,
                                usage.cache_read,
                                usage.cache_write_reported,
                            )
                        )
                    )
                if cancel.is_set():
                    raise _TurnCancelled
                if not calls:
                    if not text.strip():
                        text = EMPTY_REPLY
                        push(Event(text=text))
                    conv.add_assistant(text)
                    push(Event(done=True))
                    return
                conv.add_assistant_with_tool_calls(text, calls)
                unknown_run = (
                    unknown_run + 1
                    if all(self._registry.get(c.name) is None for c in calls)
                    else 0
                )
                results = [ToolResult(c.id, NOTICE_CANCELLED, True) for c in calls]
                try:
                    await self._execute_batched(calls, results, mode, cancel, push)
                finally:
                    # 包括未开始和执行中的调用，任何退出路径均有对应结果。
                    conv.add_tool_results(results)
                if cancel.is_set():
                    raise _TurnCancelled
                if unknown_run >= MAX_UNKNOWN_RUN:
                    ensure_assistant_tail(conv, NOTICE_UNKNOWN_TOOLS)
                    push(Event(notice=NOTICE_UNKNOWN_TOOLS))
                    push(Event(done=True))
                    return
            ensure_assistant_tail(conv, NOTICE_MAX_ITER)
            push(Event(notice=NOTICE_MAX_ITER))
            push(Event(done=True))
        except _TurnCancelled:
            ensure_assistant_tail(conv, NOTICE_CANCELLED)
        except asyncio.CancelledError:
            ensure_assistant_tail(conv, NOTICE_CANCELLED)
            raise
        except Exception as exc:
            ensure_assistant_tail(conv, NOTICE_STREAM_ERR)
            push(Event(err=exc, notice=NOTICE_STREAM_ERR))

    async def _stream_once(
        self,
        conv: Conversation,
        definitions: list[ToolDefinition],
        system: System,
        reminder: str,
        cancel: asyncio.Event,
        push: Push,
    ) -> tuple[str, list[ToolCall], LLMUsage | None]:
        text = ""
        calls: list[ToolCall] = []
        usage = None
        async with aclosing(
            self._provider.stream(
                Request(conv.messages(), definitions, system, reminder)
            )
        ) as stream:
            while True:
                try:
                    event = await _until_cancel(anext(stream), cancel)
                except StopAsyncIteration:
                    raise RuntimeError("响应流未正常结束，请重新发送问题") from None
                if event.err is not None:
                    raise event.err
                if event.text:
                    text += event.text
                    push(Event(text=event.text))
                calls.extend(event.tool_calls)
                if event.usage is not None:
                    usage = event.usage
                if event.done:
                    return text, calls, usage

    async def _execute_batched(
        self,
        calls: list[ToolCall],
        results: list[ToolResult],
        mode: Mode,
        cancel: asyncio.Event,
        push: Push,
    ) -> None:
        async def run_one(index: int) -> None:
            call = calls[index]
            if mode == Mode.PLAN and not self._registry.is_read_only(call.name):
                results[index] = ToolResult(
                    call.id, "计划模式仅允许只读工具，请使用 /do 开始执行。", True
                )
                return
            result = await self._registry.execute(call.name, call.input)
            results[index] = ToolResult(call.id, result.content, result.is_error)

        i = 0
        while i < len(calls):
            if cancel.is_set():
                raise _TurnCancelled
            j = i + 1
            if self._registry.is_read_only(calls[i].name):
                while j < len(calls) and self._registry.is_read_only(calls[j].name):
                    j += 1
            for call in calls[i:j]:
                push(Event(tool=ToolEvent(call.name, args_preview(call))))
            try:
                await _until_cancel(
                    asyncio.gather(*(run_one(k) for k in range(i, j))), cancel
                )
            finally:
                # 完成事件按调用顺序发出，避免并发完成时交错滚动记录。
                for call, result in zip(calls[i:j], results[i:j], strict=True):
                    push(
                        Event(
                            tool=ToolEvent(
                                call.name,
                                args_preview(call),
                                Phase.END,
                                result.content,
                                result.is_error,
                            )
                        )
                    )
            i = j
