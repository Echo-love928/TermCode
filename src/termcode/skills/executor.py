"""在当前会话钉住 SOP，或用独立 Agent 运行 Skill。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from termcode.compact import SessionContext
from termcode.config import ProviderConfig
from termcode.conversation import Conversation
from termcode.llm import Message, new_provider
from termcode.permission import Mode, Outcome
from termcode.skills.parser import SkillDef, substitute_arguments

if TYPE_CHECKING:
    from termcode.agent import Agent, ApprovalRequest, Event

SYSTEM_TOOL_NAMES = frozenset({"LoadSkill"})


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
        fork_conv = Conversation.from_messages(_build_fork_context(conv, skill.context))
        body = self._render(skill, args)
        if args and "$ARGUMENTS" not in skill.prompt_body:
            body += f"\n\n## User Request\n\n{args}"
        fork_conv.add_user(body)
        provider = self.agent.provider
        if skill.model and skill.model != provider.model and self.config:
            provider = new_provider(replace(self.config, model=skill.model))
        try:
            session_dir = self.agent.runtime.session.session_dir
            root = Path(session_dir).parent if session_dir else None
            try:
                temporary = TemporaryDirectory(prefix=".fork-", dir=root)
            except OSError:
                if root is None:
                    raise
                temporary = TemporaryDirectory(prefix=".fork-")
            with temporary as spill_dir:
                session = SessionContext("", spill_dir)
                child = self.agent.fork(provider, SYSTEM_TOOL_NAMES, session)
                async with aclosing(child.run(fork_conv, mode, cancel)) as events:
                    async for event in events:
                        yield event
        finally:
            if provider is not self.agent.provider:
                await provider.aclose()

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

        parts = []
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
                if event.err is not None:
                    raise event.err
                if event.usage is not None and push is not None:
                    push(event)
                if event.notice:
                    parts.append(f"\n{event.notice}")
                if event.text:
                    parts.append(event.text)
                if event.done:
                    return "".join(parts)
        return NOTICE_CANCELLED
