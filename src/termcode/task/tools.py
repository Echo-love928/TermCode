import json
from dataclasses import asdict
from typing import Any

from termcode.tool import Result, arguments, string_schema

from .manager import BackgroundTask, Manager


def _summary(task: BackgroundTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "name": task.name,
        "status": task.status,
        "tool_count": task.tool_count,
        "last_activity": task.last_activity,
    }


def _json(value: Any) -> Result:
    return Result(json.dumps(value, ensure_ascii=False))


class _TaskTool:
    is_system = True
    read_only = False

    def __init__(self, manager: Manager) -> None:
        self.manager = manager

    def parameters(self) -> dict[str, Any]:
        return string_schema({"task_id": "后台任务 ID"})

    async def execute(self, args: str) -> Result:
        try:
            data = arguments(args, self.parameters())
            for key in self.parameters()["required"]:
                if not data[key].strip():
                    raise ValueError(f"参数 {key} 不得为空")
            return await self._execute(data)
        except (ValueError, RecursionError) as exc:
            return Result(str(exc), is_error=True)

    async def _execute(self, data: dict[str, str]) -> Result:
        raise NotImplementedError


class TaskListTool(_TaskTool):
    read_only = True

    def name(self) -> str:
        return "TaskList"

    def description(self) -> str:
        return "列出当前会话的后台子 Agent 任务及运行状态。"

    def parameters(self) -> dict[str, Any]:
        return string_schema({})

    async def _execute(self, data: dict[str, str]) -> Result:
        return _json([_summary(task) for task in self.manager.list()])


class TaskGetTool(_TaskTool):
    read_only = True

    def name(self) -> str:
        return "TaskGet"

    def description(self) -> str:
        return "按 ID 查询后台任务的进度、用量、结果及错误。"

    async def _execute(self, data: dict[str, str]) -> Result:
        task = self.manager.get(data["task_id"])
        if task is None:
            return Result(f"未知后台任务 {data['task_id']}", is_error=True)
        return _json(
            {
                **_summary(task),
                "task": task.task,
                "result": task.result,
                "err": str(task.err) if task.err is not None else None,
                "start_time": task.start_time,
                "end_time": task.end_time,
                "usage": asdict(task.usage),
            }
        )


class TaskStopTool(_TaskTool):
    def name(self) -> str:
        return "TaskStop"

    def description(self) -> str:
        return "请求取消正在运行的后台任务。"

    async def _execute(self, data: dict[str, str]) -> Result:
        task_id = data["task_id"]
        if not await self.manager.stop(task_id):
            return Result(f"后台任务 {task_id} 不存在或已结束", is_error=True)
        return _json({"task_id": task_id, "status": "cancellation_requested"})


class SendMessageTool(_TaskTool):
    def name(self) -> str:
        return "SendMessage"

    def description(self) -> str:
        return "按名称向已完成的后台子 Agent 续派任务，保留其对话历史。"

    def parameters(self) -> dict[str, Any]:
        return string_schema({"name": "后台子 Agent 名称", "message": "新任务"})

    async def _execute(self, data: dict[str, str]) -> Result:
        task_id = await self.manager.send_message(data["name"], data["message"])
        return _json({"task_id": task_id, "status": "resumed"})
