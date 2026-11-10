"""团队任务存储和双向依赖维护。"""

import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from ..persistence import atomic_json, file_lock, read_json


class Status(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


@dataclass
class Task:
    title: str
    description: str = ""
    assignee: str = ""
    status: Status = Status.PENDING
    id: str = ""
    blocked_by: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Filter:
    status: Status | None = None


@dataclass(frozen=True)
class Patch:
    title: str | None = None
    description: str | None = None
    assignee: str | None = None
    status: Status | None = None
    add_blocks: list[str] = field(default_factory=list)
    add_blocked_by: list[str] = field(default_factory=list)
    remove_blocks: list[str] = field(default_factory=list)
    remove_blocked_by: list[str] = field(default_factory=list)


class Store:
    def __init__(self, config_dir: Path) -> None:
        self.path = config_dir / "tasks.json"
        self.lock = config_dir / "tasks.lock"

    def _load(self) -> dict[str, Task]:
        try:
            return {
                item["id"]: Task(**(item | {"status": Status(item["status"])}))
                for item in read_json(self.path)["tasks"]
            }
        except FileNotFoundError:
            return {}

    def _save(self, tasks: dict[str, Task]) -> None:
        atomic_json(self.path, {"tasks": [asdict(task) for task in tasks.values()]})

    def _require(self, tasks: dict[str, Task], task_id: str) -> Task:
        if task_id not in tasks:
            raise ValueError(f"找不到团队任务 {task_id}")
        return tasks[task_id]

    def _edge(
        self, tasks: dict[str, Task], blocker: str, blocked: str, add: bool
    ) -> None:
        if blocker == blocked:
            raise ValueError("任务不能依赖自身")
        before = self._require(tasks, blocker)
        after = self._require(tasks, blocked)
        for task, edges, value in (
            (before, before.blocks, blocked),
            (after, after.blocked_by, blocker),
        ):
            if add and value not in edges:
                edges.append(value)
                task.updated_at = time.time()
            elif not add and value in edges:
                edges.remove(value)
                task.updated_at = time.time()

    async def create(self, task: Task) -> str:
        if not task.title.strip():
            raise ValueError("任务标题不能为空")
        async with file_lock(self.lock):
            tasks = self._load()
            task_id = f"task_{uuid4().hex[:6]}"
            while task_id in tasks:
                task_id = f"task_{uuid4().hex[:6]}"
            tasks[task_id] = Task(
                task.title,
                task.description,
                task.assignee,
                task.status,
                task_id,
            )
            for blocker in task.blocked_by:
                self._edge(tasks, blocker, task_id, True)
            for blocked in task.blocks:
                self._edge(tasks, task_id, blocked, True)
            self._save(tasks)
        return task_id

    async def get(self, task_id: str) -> Task:
        return self._require(self._load(), task_id)

    async def list_(self, filter_: Filter | None = None) -> list[dict[str, object]]:
        tasks = self._load()
        completed = {
            key for key, task in tasks.items() if task.status == Status.COMPLETED
        }
        return [
            asdict(task) | {"is_ready": completed.issuperset(task.blocked_by)}
            for task in tasks.values()
            if filter_ is None
            or filter_.status is None
            or task.status == filter_.status
        ]

    async def update(self, task_id: str, patch: Patch) -> Task:
        async with file_lock(self.lock):
            tasks = self._load()
            task = self._require(tasks, task_id)
            for name in ("title", "description", "assignee", "status"):
                value = getattr(patch, name)
                if value is not None:
                    setattr(task, name, value)
            if not task.title.strip():
                raise ValueError("任务标题不能为空")
            for other in patch.remove_blocked_by:
                self._edge(tasks, other, task_id, False)
            for other in patch.remove_blocks:
                self._edge(tasks, task_id, other, False)
            for other in patch.add_blocked_by:
                self._edge(tasks, other, task_id, True)
            for other in patch.add_blocks:
                self._edge(tasks, task_id, other, True)
            task.updated_at = time.time()
            self._save(tasks)
        return task
