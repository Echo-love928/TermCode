"""管理同一会话内可查询、取消和续派的子 Agent。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import uuid4

from termcode.agent import ApprovalRequest, Event, MaxTurnsReached, Phase
from termcode.permission import Outcome

if TYPE_CHECKING:
    from termcode.agent import Agent
    from termcode.agent.agent import Push
    from termcode.conversation import Conversation


class Status(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskNotFound(ValueError):
    pass


class TaskBusy(ValueError):
    pass


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0


@dataclass
class BackgroundTask:
    sub_agent: Agent
    conv: Conversation
    name: str
    task: str
    cleanup: Callable[[], Awaitable[None]] | None = field(default=None, repr=False)
    id: str = field(default_factory=lambda: f"task_{uuid4().hex[:16]}")
    status: Status = Status.RUNNING
    result: str = ""
    err: BaseException | None = None
    start_time: float = field(default_factory=time.monotonic)
    end_time: float = 0.0
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    handle: asyncio.Task[str] | None = field(default=None, repr=False)
    usage: Usage = field(default_factory=Usage)
    tool_count: int = 0
    last_activity: str = ""
    approval: ApprovalRequest | None = field(default=None, repr=False)
    detach: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    inline_push: Push | None = field(default=None, repr=False)


def build_task_notification(task: BackgroundTask) -> str:
    name = f' (name="{task.name}")' if task.name else ""
    lines = ["<task-notification>", f"Task {task.id}{name}: {task.status}"]
    if task.err is not None:
        lines.append(f"Error: {task.err}")
    if task.result:
        lines.append(f"Result: {task.result}")
    return "\n".join([*lines, "</task-notification>"])


class Manager:
    def __init__(self) -> None:
        self._tasks: dict[str, BackgroundTask] = {}
        self._by_name: dict[str, str] = {}
        self._done: asyncio.Queue[str] = asyncio.Queue()
        self._notifications: dict[str, deque[str]] = defaultdict(deque)
        self.foreground: BackgroundTask | None = None
        self.approval_sink: Callable[[ApprovalRequest], None] | None = None

    def _register(self, task: BackgroundTask) -> None:
        self._tasks[task.id] = task
        if task.name:
            self._by_name[task.name] = task.id

    def _push(self, task: BackgroundTask, event: Event | ApprovalRequest) -> None:
        if isinstance(event, ApprovalRequest):
            task.approval = event
            if task.inline_push is not None:
                task.inline_push(event)
            else:
                self._ask_in_background(event)
        elif event.tool is not None and event.tool.phase == Phase.START:
            task.tool_count += 1
            task.last_activity = event.tool.name
        elif event.usage is not None:
            task.usage.input += event.usage.input
            task.usage.output += event.usage.output
            task.usage.cache_write += event.usage.cache_write
            task.usage.cache_read += event.usage.cache_read

    def _ask_in_background(self, request: ApprovalRequest) -> None:
        request.background = True
        if self.approval_sink is not None:
            self.approval_sink(request)
        else:
            request.respond.set_result(Outcome.DENY_ONCE)

    def _start(self, task: BackgroundTask, text: str) -> asyncio.Task[str]:
        handle = asyncio.create_task(
            task.sub_agent.run_to_completion(
                task.conv,
                text,
                cancel=task.cancel_event,
                push=lambda event: self._push(task, event),
            )
        )
        task.handle = handle
        handle.add_done_callback(lambda done: self._finish(task, done))
        return handle

    def _finish(self, task: BackgroundTask, handle: asyncio.Task[str]) -> None:
        if handle.cancelled():
            task.status = Status.CANCELLED
        elif (error := handle.exception()) is not None:
            task.status, task.err = Status.FAILED, error
            if isinstance(error, MaxTurnsReached):
                task.result = error.final_text
        else:
            task.status, task.result = Status.COMPLETED, handle.result()
        task.end_time = time.monotonic()
        task.approval = None
        if task.id in self._tasks:
            self._notifications[task.id].append(build_task_notification(task))
            self._done.put_nowait(task.id)

    async def launch(
        self,
        ag: Agent,
        conv: Conversation,
        name: str,
        task: str,
        *,
        cleanup: Callable[[], Awaitable[None]] | None = None,
        prompt: str | None = None,
    ) -> str:
        entry = BackgroundTask(ag, conv, name, task, cleanup)
        self._register(entry)
        self._start(entry, task if prompt is None else prompt)
        return entry.id

    def start_foreground(self, task: BackgroundTask, push: Push) -> asyncio.Task[str]:
        if self.foreground is not None:
            raise TaskBusy("已有前台子 Agent 正在执行")
        task.inline_push = push
        self.foreground = task
        return self._start(task, task.task)

    def adopt_running(self, task: BackgroundTask, allowed_tools: list[str]) -> str:
        task.sub_agent.restrict_tools(allowed_tools)
        task.inline_push = None
        self._register(task)
        if task.approval is not None and not task.approval.respond.done():
            if task.approval.name in allowed_tools:
                self._ask_in_background(task.approval)
            else:
                task.approval.respond.set_result(Outcome.DENY_ONCE)
        return task.id

    def request_detach(self) -> bool:
        task = self.foreground
        if task is None or task.handle is None or task.handle.done():
            return False
        task.detach.set()
        return True

    def get(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    def list(self) -> list[BackgroundTask]:
        return sorted(self._tasks.values(), key=lambda task: task.start_time)

    async def stop(self, task_id: str) -> bool:
        task = self.get(task_id)
        if task is None or task.handle is None or task.handle.done():
            return False
        if not task.cancel_event.is_set():
            task.cancel_event.set()
            task.handle.cancel()
        return True

    async def send_message(self, name: str, message: str) -> str:
        task = self.get(self._by_name.get(name, ""))
        if task is None:
            raise TaskNotFound(f"未找到名为 {name} 的后台子 Agent")
        if task.status != Status.COMPLETED:
            raise TaskBusy(
                f"子 Agent {name} 当前状态为 {task.status}，只能续派已完成的任务"
            )
        task.status, task.result, task.err = Status.RUNNING, "", None
        task.end_time = 0.0
        task.cancel_event = asyncio.Event()
        self._start(task, message)
        return task.id

    def subscribe_done(self) -> asyncio.Queue[str]:
        return self._done

    def take_notification(self, task_id: str) -> str:
        notices = self._notifications.get(task_id)
        if not notices:
            return ""
        notice = notices.popleft()
        if not notices:
            del self._notifications[task_id]
        return notice

    async def clear(self) -> None:
        tasks = self.list()
        for task in tasks:
            await self.stop(task.id)
        await asyncio.gather(
            *(task.handle for task in tasks if task.handle is not None),
            return_exceptions=True,
        )
        for task in tasks:
            if task.cleanup is not None:
                try:
                    await task.cleanup()
                except Exception:
                    logging.getLogger(__name__).warning(
                        "无法释放子 Agent %s 的资源", task.id
                    )
        self._tasks.clear()
        self._by_name.clear()
        self._notifications.clear()
        while not self._done.empty():
            self._done.get_nowait()
