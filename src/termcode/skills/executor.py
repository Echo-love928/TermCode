"""在当前会话钉住 SOP，或用独立 Agent 运行 Skill。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from termcode.config import ProviderConfig
from termcode.conversation import Conversation
from termcode.llm import Message
from termcode.permission import Mode, Outcome
from termcode.permission.settings import FRIENDLY_NAMES
from termcode.skills.parser import SkillDef, substitute_arguments

if TYPE_CHECKING:
    from termcode.agent import Agent, ApprovalRequest, Event


def _build_fork_context(conv: Conversation, context: str) -> list[Message]:
    if context == "none":
        return []
    # 只复制对话文字，避免拆断供应商要求成对出现的工具调用和结果。
    messages = [
        Message(msg.role, msg.content)
        for msg in conv.messages()
        if msg.role in ("user", "assistant") and msg.content
    ]
    if context == "recent":
        return messages[-5:]
    if not messages:
        return []
    summary = "\n\n".join(f"{msg.role}: {msg.content}" for msg in messages)
    return [Message("user", "## Previous conversation summary\n\n" + summary)]


class SkillExecutor:
    def __init__(self, agent: Agent, config: ProviderConfig | None = None) -> None:
        self.agent = agent
        self.config = config

    @staticmethod
    def _render(skill: SkillDef, args: str) -> str:
        body = substitute_arguments(skill.prompt_body, args)
        if skill.is_directory:
            body += (
                f"\n\nSkill resource directory: {skill.source_path.parent.as_posix()}"
            )
        return body

    def execute_inline(self, skill: SkillDef, args: str) -> None:
        self.agent.activate_skill(skill.name, self._render(skill, args))

    async def stream_fork(
        self,
        skill: SkillDef,
        args: str,
        conv: Conversation,
        mode: Mode,
        cancel: asyncio.Event | None,
    ) -> AsyncGenerator[Event | ApprovalRequest, None]:
        from termcode.agent import Event

        queue: asyncio.Queue[Event | ApprovalRequest | None] = asyncio.Queue()

        async def run() -> str:
            def forward(event: Event | ApprovalRequest) -> None:
                if not isinstance(event, Event) or not (event.done or event.err):
                    queue.put_nowait(event)

            try:
                return await self._run_fork(skill, args, conv, mode, cancel, forward)
            finally:
                queue.put_nowait(None)

        worker = asyncio.create_task(run())
        try:
            while (event := await queue.get()) is not None:
                yield event
            if not worker.cancelled():
                yield Event(done=True, final_text=await worker)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def _run_fork(
        self,
        skill: SkillDef,
        args: str,
        conv: Conversation,
        mode: Mode,
        cancel: asyncio.Event | None,
        push: Callable[[Event | ApprovalRequest], None],
    ) -> str:
        from termcode.agent.launch import prepare_child
        from termcode.subagent import Definition

        fork_conv = Conversation.from_messages(_build_fork_context(conv, skill.context))
        body = self._render(skill, args)
        if args and "$ARGUMENTS" not in skill.prompt_body:
            body += f"\n\n## User Request\n\n{args}"
        fork_conv.add_user(body)
        tools = skill.frontmatter.get(
            "allowed-tools", skill.frontmatter.get("allowed_tools", [])
        )
        if isinstance(tools, str):
            tools = tools.replace(",", " ").split()
        if not isinstance(tools, list) or not all(
            isinstance(tool, str) for tool in tools
        ):
            raise ValueError("Skill allowed-tools 必须是工具名列表")
        aliases = {friendly: internal for internal, friendly in FRIENDLY_NAMES.items()}
        definition = Definition(
            f"skill-fork-{skill.name}",
            skill.description,
            tools=[aliases.get(tool, tool) for tool in cast(list[str], tools)],
            disallowed_tools=["LoadSkill"],
            dont_ask=self.agent.dont_ask,
        )
        config = None
        if skill.model and skill.model != self.agent.provider.model and self.config:
            config = replace(self.config, model=skill.model)
        child = await prepare_child(
            self.agent,
            definition,
            "skill",
            skill.name,
            provider_config=config,
            permission_mode=mode,
        )
        try:
            return await child.agent.run_to_completion(
                fork_conv, "", cancel=cancel, push=push
            )
        finally:
            await child.aclose()

    async def execute_fork(
        self,
        skill: SkillDef,
        args: str,
        conv: Conversation,
        mode: Mode = Mode.DEFAULT,
        cancel: asyncio.Event | None = None,
        push: Callable[[Event | ApprovalRequest], None] | None = None,
    ) -> str:
        from termcode.agent import NOTICE_CANCELLED, ApprovalRequest

        async with aclosing(
            self.stream_fork(skill, args, conv, mode, cancel)
        ) as events:
            async for event in events:
                if isinstance(event, ApprovalRequest):
                    if push is None:
                        event.respond.set_result(Outcome.DENY_ONCE)
                    else:
                        push(event)
                    continue
                if event.usage is not None and push is not None:
                    push(event)
                if event.done:
                    return event.final_text or ""
        return NOTICE_CANCELLED
