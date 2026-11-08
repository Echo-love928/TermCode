"""协议无关的 ReAct 循环编排与保序分批并发。"""

import asyncio
import json
import logging
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

from termcode import __version__, prompt
from termcode.agent.event import CompactEvent, CompactPhase
from termcode.agent.runtime import SessionRuntime
from termcode.compact import (
    ManageInput,
    ManageOutput,
    SessionContext,
    TriggerKind,
    manage_context,
)
from termcode.compact.const import MANUAL_SAFETY_MARGIN
from termcode.compact.token import estimate_tokens, usage_anchor
from termcode.conversation import Conversation
from termcode.hook import DispatchResult
from termcode.hook import Engine as HookEngine
from termcode.hook import Event as HookEvent
from termcode.hook.rule import Payload
from termcode.llm import (
    PromptTooLongError,
    Provider,
    Request,
    System,
    ToolCall,
    ToolDefinition,
    ToolResult,
    error_message,
)
from termcode.llm import Usage as LLMUsage
from termcode.memory import Manager
from termcode.permission import Decision, Engine, Mode, Outcome, new_engine
from termcode.prompt.environment import build_environment_context
from termcode.tool import Registry
from termcode.tool.load_skill import LoadSkill

MAX_ITERATIONS = 25
MAX_UNKNOWN_RUN = 3
PLAN_REMINDER_INTERVAL = 4
NOTICE_MAX_ITER = "（已达最大迭代轮数 25，自动停止；可继续发消息推进。）"
NOTICE_UNKNOWN_TOOLS = "（连续多轮只请求到未注册的工具，自动停止。）"
NOTICE_STREAM_ERR = "（请求出错，本轮已中断。）"
NOTICE_CANCELLED = "（已取消。）"
EMPTY_REPLY = "（模型未返回正文）"
MEMORY_SIGNAL = re.compile(
    r"记住|记忆|别忘|remember|memo|更正|纠正|修正|"
    r"我(?:一名|(?:现在|目前|其实|已经)?(?:是(?!谁|什么|否)|叫|在读|毕业|从事))|"
    r"我的(?:身份|职业|工作|名字|姓名)|"
    r"\b(?:i\s+am|i['’]m|my\s+(?:name|role|job|profession)\s+is)\b",
    re.IGNORECASE,
)


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
    final_text: str | None = None


@dataclass
class ApprovalRequest:
    name: str
    args: str
    reason: str
    respond: asyncio.Future[Outcome]
    background: bool = False


class _TurnCancelled(Exception):
    """本轮取消，由主循环统一维护历史并收尾。"""


Push = Callable[[Event | ApprovalRequest], None]
ApprovalUpgrader = Callable[[ApprovalRequest], Awaitable[tuple[Outcome, bool]]]
QuerySource = Literal["main", "defined", "fork", "skill"]


class MaxTurnsReached(RuntimeError):
    def __init__(self, turns: int, final_text: str) -> None:
        super().__init__(f"子 Agent 已达最大迭代轮数 {turns}")
        self.final_text = final_text


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
        memory_manager: Manager | None = None,
        instruction_text: str = "",
        memory_text: str = "",
        hook_engine: HookEngine | None = None,
        system_prompt: str | None = None,
        max_turns: int = 0,
        permission_mode: Mode | None = None,
        dont_ask: bool = False,
        approval_upgrader: ApprovalUpgrader | None = None,
        parent_engine: Engine | None = None,
        query_source: QuerySource = "main",
        label: str = "",
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._version = version
        self.engine = engine if engine is not None else new_engine(str(Path.cwd()))[0]
        self.runtime = runtime if runtime is not None else SessionRuntime()
        if hook_engine is not None:
            self.runtime.hook_engine = hook_engine
        self._mem_mgr = memory_manager
        self._instruction_text = instruction_text
        self._memory_text = memory_text
        self.active_skills = self.runtime.active_skills
        self._skill_catalog = ""
        self.system_prompt = system_prompt
        self.max_turns = max_turns or MAX_ITERATIONS
        self.permission_mode = permission_mode
        self.dont_ask = dont_ask
        self.approval_upgrader = approval_upgrader
        self.parent_engine = parent_engine
        self.query_source = query_source
        self.label = label
        # 同一会话的普通对话和手动压缩必须串行。
        self._run_lock = self.runtime.run_lock

    def activate_skill(self, name: str, prompt_body: str) -> None:
        self.active_skills[name] = prompt_body

    def clear_active_skills(self) -> None:
        self.active_skills.clear()

    def set_skill_catalog(self, catalog: str) -> None:
        self._skill_catalog = catalog

    def spawn_child(
        self,
        provider: Provider,
        allowed_tools: list[str],
        session: SessionContext,
        *,
        context_window: int,
        system_prompt: str | None,
        max_turns: int,
        permission_mode: Mode,
        dont_ask: bool,
        query_source: QuerySource,
        label: str,
    ) -> "Agent":
        child = Agent(
            provider,
            self._registry,
            self._version,
            self.engine,
            runtime=SessionRuntime(session=session, context_window=context_window),
            instruction_text=self._instruction_text,
            memory_text=self._mem_mgr.load_index()
            if self._mem_mgr
            else self._memory_text,
            hook_engine=self.runtime.hook_engine,
            system_prompt=system_prompt,
            max_turns=max_turns,
            permission_mode=permission_mode,
            dont_ask=dont_ask,
            parent_engine=self.engine,
            query_source=query_source,
            label=label,
        )
        child.restrict_tools(allowed_tools)
        child._skill_catalog = self._skill_catalog
        if query_source == "fork":
            child.active_skills.update(self.active_skills)
        return child

    def restrict_tools(self, allowed: list[str]) -> None:
        self._registry = self._registry.without(
            frozenset(self.tool_names()) - set(allowed)
        )

    def tool_names(self) -> list[str]:
        return [definition.name for definition in self._registry.definitions()]

    @property
    def provider(self) -> Provider:
        return self._provider

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

    async def _dispatch_hook(
        self,
        event: HookEvent,
        mode: Mode,
        payload: Payload,
        cancel: asyncio.Event | None = None,
    ) -> DispatchResult:
        if self.runtime.hook_engine is None:
            return DispatchResult()
        operation = self.runtime.dispatch_hook(event, self.engine.root, mode, payload)
        return (
            await _until_cancel(operation, cancel)
            if cancel is not None
            else await operation
        )

    async def _manage_with_hooks(self, in_: ManageInput, mode: Mode) -> ManageOutput:
        payload = {"trigger": in_.trigger.value}
        await self._dispatch_hook(HookEvent.PRE_COMPACT, mode, payload)
        out = await manage_context(in_)
        if out.summarized or in_.trigger is TriggerKind.MANUAL:
            # 历史提交和锚点重置之间不让出执行权，取消也不会留下旧锚点。
            self._reset_anchor()
        await self._dispatch_hook(
            HookEvent.POST_COMPACT,
            mode,
            {
                **payload,
                "before_tokens": out.before_tokens,
                "after_tokens": out.after_tokens,
            },
        )
        return out

    async def _notify_stream_error(
        self, exc: Exception, mode: Mode, cancel: asyncio.Event
    ) -> None:
        status = getattr(exc, "status_code", None)
        # 供应商的错误正文可能包含请求内容或密钥，通知只发送摘要。
        if isinstance(status, int):
            detail = f"{type(exc).__name__} (HTTP {status})"
        elif isinstance(exc, (RuntimeError, PromptTooLongError)):
            detail = str(exc)
        else:
            detail = type(exc).__name__
        await self._dispatch_hook(
            HookEvent.NOTIFICATION,
            mode,
            {"kind": "stream_error", "detail": detail},
            cancel,
        )

    async def run_force_compact(
        self,
        conv: Conversation,
        tool_defs: list[ToolDefinition],
        mode: Mode = Mode.DEFAULT,
    ) -> tuple[int, int]:
        async with self._run_lock:
            out = await self._manage_with_hooks(
                self._manage_input(conv, tool_defs, TriggerKind.MANUAL), mode
            )
            return out.before_tokens, out.after_tokens

    async def _manage(
        self,
        conv: Conversation,
        definitions: list[ToolDefinition],
        trigger: TriggerKind,
        mode: Mode,
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

        try:
            out = await _until_cancel(self._manage_with_hooks(in_, mode), cancel)
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
        if self.permission_mode is not None:
            mode = self.permission_mode
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

    async def run_to_completion(
        self,
        conv: Conversation,
        task: str,
        events: asyncio.Queue[Event | ApprovalRequest] | None = None,
        *,
        cancel: asyncio.Event | None = None,
        push: Push | None = None,
    ) -> str:
        cancel = cancel if cancel is not None else asyncio.Event()
        error: Exception | None = None

        def forward(event: Event | ApprovalRequest) -> None:
            nonlocal error
            if isinstance(event, Event) and event.err is not None:
                error = event.err
            if events is not None:
                events.put_nowait(event)
            if push is not None:
                push(event)
            if events is None and push is None and isinstance(event, ApprovalRequest):
                event.respond.set_result(Outcome.DENY_ONCE)

        async with self._run_lock:
            if task:
                conv.add_user(task)
            mode = (
                self.permission_mode
                if self.permission_mode is not None
                else Mode.DEFAULT
            )
            result = await self._loop(conv, mode, cancel, forward, completion=True)
        if error is not None:
            raise error
        if cancel.is_set():
            raise asyncio.CancelledError
        if result is None:
            raise RuntimeError("子 Agent 未完成")
        return result

    async def _loop(
        self,
        conv: Conversation,
        mode: Mode,
        cancel: asyncio.Event,
        push: Push,
        *,
        completion: bool = False,
    ) -> str | None:
        unknown_run = 0
        err: Exception | None = None

        async def finish(iteration: int, final_text: str | None = None) -> None:
            await self._dispatch_hook(HookEvent.STOP, mode, {"iter": iteration}, cancel)
            push(Event(done=True, final_text=final_text))

        try:
            if cancel.is_set():
                raise _TurnCancelled
            environment = await _until_cancel(
                asyncio.to_thread(
                    prompt.gather_environment, self._version, self._provider.model
                ),
                cancel,
            )
            memory = self._mem_mgr.load_index() if self._mem_mgr else self._memory_text
            stable = self.system_prompt
            if stable is None:
                stable = prompt.build_system_prompt(self._instruction_text, memory)
            user_prompt = ""
            if self.runtime.hook_engine is not None:
                user_prompt = next(
                    (
                        msg.content
                        for msg in reversed(conv.messages())
                        if msg.role == "user"
                    ),
                    "",
                )
            text = ""
            for iteration in range(1, self.max_turns + 1):
                if cancel.is_set():
                    raise _TurnCancelled
                system = System(
                    stable,
                    build_environment_context(
                        environment, self.active_skills, self._skill_catalog
                    ),
                )
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
                await self._manage(
                    conv, definitions, TriggerKind.AUTO, mode, cancel, push
                )
                text, calls, usage, err = await self._stream_once(
                    conv, definitions, system, reminder, mode, user_prompt, cancel, push
                )
                if isinstance(err, PromptTooLongError):
                    await self._manage(
                        conv, definitions, TriggerKind.EMERGENCY, mode, cancel, push
                    )
                    estimated = estimate_tokens(0, conv.messages(), 0)
                    if estimated >= self.runtime.context_window - MANUAL_SAFETY_MARGIN:
                        raise err
                    # 同一迭代只重试一次，不回到自动压缩入口。
                    text, calls, usage, err = await self._stream_once(
                        conv,
                        definitions,
                        system,
                        reminder,
                        mode,
                        user_prompt,
                        cancel,
                        push,
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
                    self.runtime.turn_count += 1
                    recent = conv.messages()
                    starts = [i for i, msg in enumerate(recent) if msg.role == "user"]
                    periodic = self.runtime.turn_count % 5 == 0
                    latest = recent[starts[-1]].content if starts else ""
                    if (
                        not completion
                        and self._mem_mgr
                        and (periodic or MEMORY_SIGNAL.search(latest))
                    ):
                        # 定时覆盖整段窗口；身份陈述或纠正无需等待第五轮。
                        start = (
                            starts[-min(5, len(starts))]
                            if periodic and starts
                            else (starts[-1] if starts else 0)
                        )
                        self._mem_mgr.schedule(recent[start:])
                    if usage is not None:
                        self.runtime.usage_anchor = usage_anchor(usage)
                        self.runtime.anchor_msg_len = conv.length()
                    await finish(iteration, text)
                    return text
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
                    await self._execute_batched(
                        calls, results, mode, cancel, push, conv
                    )
                finally:
                    # 包括未开始和执行中的调用，任何退出路径均有对应结果。
                    conv.add_tool_results(results)
                if cancel.is_set():
                    raise _TurnCancelled
                if unknown_run >= MAX_UNKNOWN_RUN:
                    ensure_assistant_tail(conv, NOTICE_UNKNOWN_TOOLS)
                    push(Event(notice=NOTICE_UNKNOWN_TOOLS))
                    await finish(iteration, NOTICE_UNKNOWN_TOOLS)
                    return NOTICE_UNKNOWN_TOOLS
            notice = NOTICE_MAX_ITER.replace(
                str(MAX_ITERATIONS), str(self.max_turns), 1
            )
            ensure_assistant_tail(conv, notice)
            if completion:
                await self._dispatch_hook(
                    HookEvent.STOP, mode, {"iter": self.max_turns}, cancel
                )
                raise MaxTurnsReached(self.max_turns, text)
            push(Event(notice=notice))
            await finish(self.max_turns)
        except _TurnCancelled:
            ensure_assistant_tail(conv, NOTICE_CANCELLED)
        except asyncio.CancelledError:
            ensure_assistant_tail(conv, NOTICE_CANCELLED)
            raise
        except MaxTurnsReached:
            raise
        except Exception as exc:
            ensure_assistant_tail(conv, NOTICE_STREAM_ERR)
            try:
                if exc is not err:
                    await self._notify_stream_error(exc, mode, cancel)
            except _TurnCancelled:
                return None
            safe_error = (
                RuntimeError(error_message(exc)) if completion and exc is err else exc
            )
            push(Event(err=safe_error, notice=NOTICE_STREAM_ERR))
        return None

    async def _stream_once(
        self,
        conv: Conversation,
        definitions: list[ToolDefinition],
        system: System,
        reminder: str,
        mode: Mode,
        user_prompt: str,
        cancel: asyncio.Event,
        push: Push,
    ) -> tuple[str, list[ToolCall], LLMUsage | None, Exception | None]:
        text = ""
        calls: list[ToolCall] = []
        usage = None
        await self._dispatch_hook(
            HookEvent.PRE_USER_MESSAGE, mode, {"prompt": user_prompt}, cancel
        )
        reminder = "\n\n".join(filter(None, (reminder, *self.runtime.take_reminders())))
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
                    await self._notify_stream_error(event.err, mode, cancel)
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
        if self.label:
            reason = f"[来自 SubAgent {self.label}] {reason}"
        request = ApprovalRequest(call.name, args_preview(call), reason, respond)
        if self.approval_upgrader is not None:
            outcome, handled = await _until_cancel(
                self.approval_upgrader(request), cancel
            )
            if handled:
                return outcome
        push(request)
        return await _until_cancel(respond, cancel)

    async def _execute_batched(
        self,
        calls: list[ToolCall],
        results: list[ToolResult],
        mode: Mode,
        cancel: asyncio.Event,
        push: Push,
        conv: Conversation,
    ) -> None:
        from termcode.agent.agent_tool import AgentTool
        from termcode.agent.fork import is_fork_context

        def check_one(index: int) -> tuple[Decision, str]:
            call = calls[index]
            if call.name == "Agent":
                if self.query_source == "fork" or is_fork_context(conv.messages()):
                    return Decision.DENY, "Fork 子 Agent 不能再启动 Agent"
                if self.query_source != "main":
                    return Decision.DENY, "子 Agent 不能再启动 Agent"
            if self._registry.get(call.name) is None:
                return Decision.DENY, "未知工具，安全拒绝：" + call.name
            read_only = self._registry.is_read_only(call.name)
            if self.dont_ask:
                decision, reason = self.engine.check(
                    mode, call, read_only, dont_ask=True
                )
            else:
                decision, reason = self.engine.check(mode, call, read_only)
            if decision == Decision.DENY:
                return decision, reason
            if mode == Mode.PLAN and not read_only:
                return Decision.DENY, "计划模式仅允许只读工具，请使用 /do 开始执行。"
            if (
                decision == Decision.ASK
                and self.parent_engine is not None
                and self.parent_engine is not self.engine
            ):
                parent_decision, _ = self.parent_engine.check(
                    Mode.DEFAULT, call, read_only
                )
                if parent_decision == Decision.ALLOW:
                    return Decision.ALLOW, ""
            return decision, reason

        async def run_one(index: int, decision: Decision, reason: str) -> None:
            call = calls[index]
            if decision == Decision.ASK:
                await self._dispatch_hook(
                    HookEvent.NOTIFICATION,
                    mode,
                    {"kind": "approval", "detail": call.name},
                )
                outcome = await self.request_approval(call, reason, cancel, push)
                if outcome == Outcome.DENY_ONCE:
                    results[index] = ToolResult(call.id, "用户拒绝本次工具调用", True)
                    return
                if outcome == Outcome.ALLOW_FOREVER:
                    try:
                        await asyncio.to_thread(self.engine.persist_local_allow, call)
                    except Exception:
                        logging.getLogger(__name__).warning("永久放行规则保存失败")
            tool = self._registry.get(call.name)
            if isinstance(tool, AgentTool):
                result = await tool.execute_for_turn(call.input, self, conv, mode, push)
            elif isinstance(tool, LoadSkill):
                result = await tool.execute_for_turn(
                    call.input,
                    conv,
                    mode,
                    cancel,
                    push,
                    agent=self,
                )
            else:
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
            if self._registry.is_concurrency_safe(calls[i].name):
                while j < len(calls) and self._registry.is_concurrency_safe(
                    calls[j].name
                ):
                    j += 1
            allowed: list[tuple[int, Decision, str]] = []
            started = i
            ended = i
            payloads: dict[int, Payload] = {}

            def end_tool(index: int) -> None:
                call, result = calls[index], results[index]
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

            try:
                for k in range(i, j):
                    call = calls[k]
                    if self.runtime.hook_engine is not None:
                        try:
                            args = json.loads(call.input)
                        except (ValueError, RecursionError):
                            args = {}
                        payloads[k] = {
                            "tool_name": call.name,
                            "tool_input": args if isinstance(args, dict) else {},
                        }
                        hook = await self._dispatch_hook(
                            HookEvent.PRE_TOOL_USE, mode, payloads[k], cancel
                        )
                        if hook.blocked:
                            decision, reason = Decision.DENY, hook.block_message()
                        else:
                            decision, reason = check_one(k)
                    else:
                        decision, reason = check_one(k)
                    push(Event(tool=ToolEvent(call.name, args_preview(call))))
                    started = k + 1
                    if decision == Decision.DENY:
                        results[k] = ToolResult(call.id, reason, True)
                    else:
                        allowed.append((k, decision, reason))
                await _until_cancel(
                    asyncio.gather(*(run_one(*entry) for entry in allowed)), cancel
                )
                # 结果 Hook 和结束事件都按调用顺序发出。
                for k in range(i, j):
                    if k in payloads:
                        result = results[k]
                        await self._dispatch_hook(
                            HookEvent.POST_TOOL_USE,
                            mode,
                            {
                                **payloads[k],
                                "tool_result": result.content[:1600],
                                "is_error": result.is_error,
                            },
                            cancel,
                        )
                    end_tool(k)
                    ended = k + 1
            finally:
                for k in range(ended, started):
                    end_tool(k)
            i = j
