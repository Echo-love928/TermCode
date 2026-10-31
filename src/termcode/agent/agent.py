"""协议无关的 ReAct 循环编排与保序分批并发。"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from termcode import __version__, prompt
from termcode.agent.event import CompactEvent, CompactPhase
from termcode.agent.runtime import SessionRuntime
from termcode.compact import ManageInput, ManageOutput, TriggerKind, manage_context
from termcode.compact.const import MANUAL_SAFETY_MARGIN
from termcode.compact.token import estimate_tokens, usage_anchor
from termcode.conversation import Conversation
from termcode.llm import (
    PromptTooLongError,
    Provider,
    Request,
    System,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from termcode.llm import Usage as LLMUsage
from termcode.permission import Decision, Engine, Mode, Outcome, new_engine
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
    compact: CompactEvent | None = None


@dataclass(frozen=True)
class ApprovalRequest:
    name: str
    args: str
    reason: str
    respond: asyncio.Future[Outcome]


class _TurnCancelled(Exception):
    """本轮取消，由主循环统一维护历史并收尾。"""


Push = Callable[[Event | ApprovalRequest], None]


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
        self,
        provider: Provider,
        registry: Registry,
        version: str = __version__,
        engine: Engine | None = None,
        *,
        runtime: SessionRuntime | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._version = version
        self.engine = engine if engine is not None else new_engine(str(Path.cwd()))[0]
        self.runtime = runtime if runtime is not None else SessionRuntime()
        # 同一会话的普通对话和手动压缩必须串行。
        self._run_lock = self.runtime.run_lock

    def _manage_input(
        self,
        conv: Conversation,
        definitions: list[ToolDefinition],
        trigger: TriggerKind,
    ) -> ManageInput:
        runtime = self.runtime
        return ManageInput(
            conv,
            self._provider,
            self._provider.model,
            runtime.context_window,
            definitions,
            runtime.replacement,
            runtime.recovery,
            runtime.auto_tracking,
            runtime.session,
            runtime.usage_anchor,
            runtime.anchor_msg_len,
            estimate_tokens(
                runtime.usage_anchor, conv.messages(), runtime.anchor_msg_len
            ),
            trigger,
        )

    def _reset_anchor(self) -> None:
        self.runtime.usage_anchor = 0
        self.runtime.anchor_msg_len = 0

    async def run_force_compact(
        self, conv: Conversation, tool_defs: list[ToolDefinition]
    ) -> tuple[int, int]:
        async with self._run_lock:
            out = await manage_context(
                self._manage_input(conv, tool_defs, TriggerKind.MANUAL)
            )
            self._reset_anchor()
            return out.before_tokens, out.after_tokens

    async def _manage(
        self,
        conv: Conversation,
        definitions: list[ToolDefinition],
        trigger: TriggerKind,
        cancel: asyncio.Event,
        push: Push,
    ) -> None:
        in_ = self._manage_input(conv, definitions, trigger)
        started = False
        emergency = trigger == TriggerKind.EMERGENCY
        before_phase = (
            CompactPhase.BEFORE_EMERGENCY if emergency else CompactPhase.BEFORE_AUTO
        )
        after_phase = (
            CompactPhase.AFTER_EMERGENCY if emergency else CompactPhase.AFTER_AUTO
        )

        def on_start() -> None:
            nonlocal started
            started = True
            push(Event(compact=CompactEvent(before_phase)))

        if emergency:
            on_start()
        else:
            in_.on_start = on_start

        async def manage() -> ManageOutput:
            out = await manage_context(in_)
            if out.summarized:
                # 历史提交和锚点重置之间不让出执行权，取消也不会留下旧锚点。
                self._reset_anchor()
            return out

        try:
            out = await _until_cancel(manage(), cancel)
        except Exception as exc:
            if isinstance(exc, _TurnCancelled):
                raise
            if started:
                push(
                    Event(
                        compact=CompactEvent(after_phase, in_.estimated_token, 0, exc)
                    )
                )
            if emergency:
                raise
            # 自动摘要失败保留历史并继续正常请求；熔断计数由摘要层维护。
            logging.getLogger(__name__).warning(
                "自动上下文管理失败：%s", type(exc).__name__
            )
            return
        if out.summarized:
            push(
                Event(
                    compact=CompactEvent(
                        after_phase, out.before_tokens, out.after_tokens
                    )
                )
            )

    async def run(
        self,
        conv: Conversation,
        mode: Mode = Mode.DEFAULT,
        cancel: asyncio.Event | None = None,
    ) -> AsyncGenerator[Event | ApprovalRequest, None]:
        # 单个生产者独占历史；消费者提前关闭时仍等待其取消和配对收尾。
        cancel = cancel if cancel is not None else asyncio.Event()
        queue: asyncio.Queue[Event | ApprovalRequest | None] = asyncio.Queue()

        async def produce() -> None:
            try:
                async with self._run_lock:
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
                definitions = (
                    self._registry.read_only_definitions()
                    if mode == Mode.PLAN
                    else self._registry.definitions()
                )
                push(Event(iter=iteration))
                reminder = (
                    prompt.plan_reminder((iteration - 1) % PLAN_REMINDER_INTERVAL == 0)
                    if mode == Mode.PLAN
                    else ""
                )
                await self._manage(conv, definitions, TriggerKind.AUTO, cancel, push)
                text, calls, usage, err = await self._stream_once(
                    conv, definitions, system, reminder, cancel, push
                )
                if isinstance(err, PromptTooLongError):
                    await self._manage(
                        conv, definitions, TriggerKind.EMERGENCY, cancel, push
                    )
                    estimated = estimate_tokens(0, conv.messages(), 0)
                    if estimated >= self.runtime.context_window - MANUAL_SAFETY_MARGIN:
                        raise err
                    # 同一迭代只重试一次，不回到自动压缩入口。
                    text, calls, usage, err = await self._stream_once(
                        conv, definitions, system, reminder, cancel, push
                    )
                if err is not None:
                    raise err
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
                    if usage is not None:
                        self.runtime.usage_anchor = usage_anchor(usage)
                        self.runtime.anchor_msg_len = conv.length()
                    push(Event(done=True))
                    return
                conv.add_assistant_with_tool_calls(text, calls)
                if usage is not None:
                    self.runtime.usage_anchor = usage_anchor(usage)
                    self.runtime.anchor_msg_len = conv.length()
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
    ) -> tuple[str, list[ToolCall], LLMUsage | None, Exception | None]:
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
                    return text, calls, usage, event.err
                if event.text:
                    text += event.text
                    push(Event(text=event.text))
                calls.extend(event.tool_calls)
                if event.usage is not None:
                    usage = event.usage
                if event.done:
                    return text, calls, usage, None

    async def request_approval(
        self, call: ToolCall, reason: str, cancel: asyncio.Event, push: Push
    ) -> Outcome:
        respond: asyncio.Future[Outcome] = asyncio.get_running_loop().create_future()
        push(ApprovalRequest(call.name, args_preview(call), reason, respond))
        return await _until_cancel(respond, cancel)

    async def _execute_batched(
        self,
        calls: list[ToolCall],
        results: list[ToolResult],
        mode: Mode,
        cancel: asyncio.Event,
        push: Push,
    ) -> None:
        def check_one(index: int) -> tuple[Decision, str]:
            call = calls[index]
            if self._registry.get(call.name) is None:
                return Decision.DENY, "未知工具，安全拒绝：" + call.name
            decision, reason = self.engine.check(
                mode, call, self._registry.is_read_only(call.name)
            )
            if decision == Decision.DENY:
                return decision, reason
            if mode == Mode.PLAN and not self._registry.is_read_only(call.name):
                return Decision.DENY, "计划模式仅允许只读工具，请使用 /do 开始执行。"
            return decision, reason

        async def run_one(index: int, decision: Decision, reason: str) -> None:
            call = calls[index]
            if decision == Decision.ASK:
                outcome = await self.request_approval(call, reason, cancel, push)
                if outcome == Outcome.DENY_ONCE:
                    results[index] = ToolResult(call.id, "用户拒绝本次工具调用", True)
                    return
                if outcome == Outcome.ALLOW_FOREVER:
                    try:
                        await asyncio.to_thread(self.engine.persist_local_allow, call)
                    except Exception:
                        logging.getLogger(__name__).warning("永久放行规则保存失败")
            result = await self._registry.execute(call.name, call.input)
            results[index] = ToolResult(call.id, result.content, result.is_error)
            if call.name == "read_file" and not result.is_error:
                try:
                    args = json.loads(call.input)
                    path = await asyncio.to_thread(Path(args["path"]).resolve)
                    data = await asyncio.to_thread(path.read_bytes)
                    self.runtime.recovery.record_file(
                        str(path), data.decode("utf-8", errors="replace")
                    )
                except (OSError, ValueError, TypeError, KeyError):
                    pass

        i = 0
        while i < len(calls):
            if cancel.is_set():
                raise _TurnCancelled
            j = i + 1
            if self._registry.is_read_only(calls[i].name):
                while j < len(calls) and self._registry.is_read_only(calls[j].name):
                    j += 1
            allowed: list[tuple[int, Decision, str]] = []
            for k in range(i, j):
                call = calls[k]
                decision, reason = check_one(k)
                push(Event(tool=ToolEvent(call.name, args_preview(call))))
                if decision == Decision.DENY:
                    results[k] = ToolResult(call.id, reason, True)
                else:
                    allowed.append((k, decision, reason))
            try:
                await _until_cancel(
                    asyncio.gather(*(run_one(*entry) for entry in allowed)), cancel
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
