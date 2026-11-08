"""按需运行已登记的 Skill，不在工具结果中重复 inline SOP。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from termcode.conversation import Conversation
from termcode.permission import Mode
from termcode.skills.executor import SkillExecutor
from termcode.skills.loader import SkillLoader
from termcode.tool import Result, arguments, string_schema

if TYPE_CHECKING:
    from termcode.agent import Agent, ApprovalRequest, Event


class LoadSkill:
    read_only = True
    category = "read"
    is_concurrency_safe = False

    def __init__(self) -> None:
        self._loader: SkillLoader | None = None
        self._executor: SkillExecutor | None = None

    def name(self) -> str:
        return "LoadSkill"

    def description(self) -> str:
        return "按名称加载与用户请求匹配的 Skill，或执行隔离的 fork Skill。"

    def parameters(self) -> dict[str, Any]:
        return string_schema({"name": "Skill 名称"})

    def set_loader(self, loader: SkillLoader) -> None:
        self._loader = loader

    def set_agent(self, agent: Agent, executor: SkillExecutor | None = None) -> None:
        self._executor = executor if executor is not None else SkillExecutor(agent)

    async def execute(self, args: str) -> Result:
        return await self.execute_for_turn(args, Conversation(), Mode.DEFAULT)

    async def execute_for_turn(
        self,
        args: str,
        conv: Conversation,
        mode: Mode,
        cancel: asyncio.Event | None = None,
        push: Callable[[Event | ApprovalRequest], None] | None = None,
        *,
        agent: Agent | None = None,
    ) -> Result:
        if self._loader is None or self._executor is None:
            return Result("LoadSkill not properly initialized", is_error=True)
        executor = self._executor
        if agent is not None and executor.agent is not agent:
            executor = SkillExecutor(agent)
        try:
            name = arguments(args, self.parameters())["name"]
        except ValueError as exc:
            return Result(str(exc), is_error=True)
        skill = self._loader.get(name)
        if skill is None:
            names = ", ".join(name for name, _ in self._loader.get_catalog()) or "无"
            return Result(f"未知 Skill {name!r}。可用 Skill：{names}", is_error=True)
        if skill.mode == "inline":
            executor.execute_inline(skill, "")
            return Result(
                f"Skill '{name}' activated. SOP pinned to environment context."
            )
        request = next(
            (msg.content for msg in reversed(conv.messages()) if msg.role == "user"), ""
        )
        try:
            output = await executor.execute_fork(
                skill, request, conv, mode, cancel, push
            )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Skill '%s' fork 失败 (%s)", name, type(exc).__name__
            )
            return Result(f"Skill '{name}' fork 执行失败。", is_error=True)
        return Result(f"Skill '{name}' fork 结果：\n{output}")
