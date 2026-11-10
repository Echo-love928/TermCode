"""通过当前 Lead 或队员身份绑定团队工具，保留旧后台任务入口。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from termcode.task import SendMessageTool, TaskGetTool, TaskListTool
from termcode.tool import Result, Tool

from ..mailbox import Message, MessageType
from ..tasks import Filter, Patch, Status, Store, Task

if TYPE_CHECKING:
    from termcode.agent.team_hook import TeammateContext

    from ..manager import Manager


def json_result(value: object) -> Result:
    return Result(json.dumps(value, ensure_ascii=False))


@dataclass(frozen=True)
class Schema:
    description: str
    required: tuple[str, ...] = ()
    strings: tuple[str, ...] = ()
    arrays: tuple[str, ...] = ()
    booleans: tuple[str, ...] = ()


SCHEMAS = {
    "TeamCreate": Schema(
        "创建团队并成为 Lead。之后用 Agent 指定 team_name 和 name 派出队员。",
        ("team_name",),
        ("description", "agent_type"),
    ),
    "TeamDelete": Schema(
        "删除团队及其队员 Worktree 与会话。队员活跃时需显式 force。",
        ("team_name",),
        booleans=("force",),
    ),
    "TaskCreate": Schema(
        "创建团队共享任务，可指定负责人和前置任务。",
        ("title",),
        ("description", "assignee"),
        ("blocked_by",),
    ),
    "TaskGet": Schema("读取团队共享任务详情。", ("task_id",)),
    "TaskList": Schema("列出共享任务与依赖就绪状态。", strings=("status",)),
    "TaskUpdate": Schema(
        "更新共享任务的状态、负责人或依赖。",
        ("task_id",),
        ("title", "description", "assignee", "status"),
        ("add_blocks", "add_blocked_by", "remove_blocks", "remove_blocked_by"),
    ),
    "SendMessage": Schema(
        "用 to 和 content 发给队员名、ID、lead 或广播 *。"
        "计划提交用 plan_approval_request。"
        "审批回复或退出应答需 request_id 和 approve。",
        ("to", "content"),
        ("type", "request_id"),
        booleans=("approve",),
    ),
}


class TeamTool:
    def __init__(
        self,
        name: str,
        manager: Manager,
        team_name: str = "",
        teammate: TeammateContext | None = None,
    ) -> None:
        self._name = name
        self.manager = manager
        self.team_name = team_name
        self.teammate = teammate
        self.schema = SCHEMAS[name]
        self.read_only = name in {"TaskGet", "TaskList"}

    def name(self) -> str:
        return self._name

    def description(self) -> str:
        if self._name == "SendMessage" and self.teammate is None:
            return (
                self.schema.description
                + "原有后台子 Agent 续派仍可用 name 和 message。"
            )
        return self.schema.description

    def parameters(self) -> dict[str, Any]:
        schema = self.schema
        properties: dict[str, Any] = {
            key: {"type": "string"} for key in (*schema.required, *schema.strings)
        }
        properties.update(
            {
                key: {"type": "array", "items": {"type": "string"}}
                for key in schema.arrays
            }
        )
        properties.update({key: {"type": "boolean"} for key in schema.booleans})
        required = list(schema.required)
        if self._name == "SendMessage":
            properties["type"]["enum"] = list(MessageType)
            if self.teammate is None:
                properties.update(name={"type": "string"}, message={"type": "string"})
                required = []
        if "status" in properties:
            properties["status"]["enum"] = list(Status)
        if self.teammate is None and self._name not in {"TeamCreate", "TeamDelete"}:
            properties["team_name"] = {"type": "string"}
        return {"type": "object", "required": required, "properties": properties}

    def _parse(self, args: str) -> dict[str, Any]:
        data = json.loads(args or "{}")
        if not isinstance(data, dict):
            raise ValueError("参数必须是 JSON 对象")
        schema = self.parameters()
        for key in schema["required"]:
            if key not in data:
                raise ValueError(f"缺少必填参数：{key}")
        for key, value in data.items():
            spec = schema["properties"].get(key)
            if spec is None:
                raise ValueError(f"未知参数：{key}")
            kind = spec["type"]
            if kind == "string" and not isinstance(value, str):
                raise ValueError(f"参数 {key} 必须是字符串")
            if kind == "boolean" and type(value) is not bool:
                raise ValueError(f"参数 {key} 必须是布尔值")
            if kind == "array" and (
                not isinstance(value, list)
                or not all(isinstance(v, str) for v in value)
            ):
                raise ValueError(f"参数 {key} 必须是字符串数组")
        return data

    async def execute(self, args: str) -> Result:
        try:
            return await self._execute(self._parse(args))
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            return Result(f"团队工具 {self._name} 失败：{exc}", is_error=True)

    async def _execute(self, data: dict[str, Any]) -> Result:
        name = self._name
        if name in {"TeamCreate", "TeamDelete"} and self.teammate is not None:
            raise ValueError("只有 Lead 可以创建或删除团队")
        if name == "TeamCreate":
            team = await self.manager.create(
                data["team_name"],
                data.get("agent_type", ""),
                description=data.get("description", ""),
            )
            return json_result(
                {
                    "team_name": team.sanitized_name,
                    "backend": team.backend,
                    "config_path": str(team.config_path),
                }
            )
        if name == "TeamDelete":
            await self.manager.delete(data["team_name"], data.get("force", False))
            return Result(f"已删除团队 {data['team_name']}")

        explicit_team = data.pop("team_name", "")
        if self.teammate is None and not explicit_team:
            task_mgr = self.manager.task_mgr
            if name == "SendMessage" and "to" not in data and "name" in data:
                return await SendMessageTool(task_mgr).execute(json.dumps(data))
            if name == "TaskGet" and task_mgr.get(data["task_id"]) is not None:
                return await TaskGetTool(task_mgr).execute(json.dumps(data))
            if name == "TaskList" and not self.team_name:
                return await TaskListTool(task_mgr).execute(json.dumps(data))
        team_name = explicit_team or self.team_name
        if not team_name:
            raise ValueError("请先创建团队或指定 team_name")
        team = self.manager.require_owned(team_name)
        actor = self.teammate.agent_id if self.teammate else team.lead_agent_id
        member = team.member(actor)
        if member is None:
            raise ValueError("调用者不属于该团队")
        store = Store(team.config_dir)

        if name == "SendMessage":
            to, content = data.get("to", ""), data.get("content")
            if not to or content is None:
                raise ValueError("必须提供 to 和 content")
            kind = MessageType(data.get("type", "text"))
            request_id = data.get("request_id", "")
            if kind in {
                MessageType.PLAN_APPROVAL_REQUEST,
                MessageType.SHUTDOWN_REQUEST,
            }:
                # 请求 ID 由系统生成，队员重名或重建也无法复用旧审批。
                request_id = f"req-{member.agent_id}-{uuid4().hex}"
            message = Message(
                member.name,
                content,
                type=kind,
                request_id=request_id,
                approve=data.get("approve"),
            )
            delivered = await self.manager.deliver(
                team,
                actor,
                to,
                message,
                member_process=bool(
                    self.teammate and self.teammate.backend_type != "in-process"
                ),
            )
            if (
                self.teammate
                and kind == MessageType.SHUTDOWN_RESPONSE
                and message.approve
            ):
                self.teammate.shutdown_requested = True
            result = {"delivered_to": delivered, "timestamp": message.timestamp}
            if request_id:
                result["request_id"] = request_id
            return json_result(result)
        if name == "TaskCreate":
            return json_result({"task_id": await store.create(Task(**data))})
        if "status" in data:
            data["status"] = Status(data["status"])
        if name == "TaskList":
            return json_result(await store.list_(Filter(**data)))
        task_id = data.pop("task_id")
        if name == "TaskGet":
            return json_result(asdict(await store.get(task_id)))
        return json_result(asdict(await store.update(task_id, Patch(**data))))


def lifecycle_tools(manager: Manager) -> list[Tool]:
    return [TeamTool(name, manager) for name in ("TeamCreate", "TeamDelete")]


def collaboration_tools(
    manager: Manager, *, team_name: str, teammate: TeammateContext | None = None
) -> list[Tool]:
    return [
        TeamTool(name, manager, team_name, teammate)
        for name in ("TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "SendMessage")
    ]
