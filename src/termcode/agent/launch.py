"""Agent 工具与 Skill fork 共用的子会话构造。"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from termcode.agent.agent import Agent, QuerySource
from termcode.compact import SessionContext
from termcode.config import ProviderConfig, effective_context_window
from termcode.llm import Provider, new_provider
from termcode.permission import Mode
from termcode.subagent import Definition
from termcode.tool.filter import FilterParams, apply_agent_tool_filter


def child_tools(
    parent: Agent, definition: Definition, *, background: bool
) -> list[str]:
    from termcode.task import TaskListTool

    return apply_agent_tool_filter(
        FilterParams(
            parent.tool_names(),
            definition.source,
            background,
            definition.tools,
            definition.disallowed_tools,
            definition.is_fork(),
            legacy_task_tools=isinstance(
                parent.base_registry.get("TaskList"), TaskListTool
            ),
        )
    )


@dataclass
class Child:
    agent: Agent
    spill: TemporaryDirectory[str]
    owned_provider: Provider | None = None

    async def aclose(self) -> None:
        try:
            if self.owned_provider is not None:
                await self.owned_provider.aclose()
        finally:
            self.spill.cleanup()


async def prepare_child(
    parent: Agent,
    definition: Definition,
    source: QuerySource,
    label: str,
    *,
    background: bool = False,
    provider_config: ProviderConfig | None = None,
    permission_mode: Mode | None = None,
) -> Child:
    session_dir = parent.runtime.session.session_dir
    try:
        spill = TemporaryDirectory(
            prefix=".subagent-", dir=session_dir or parent.engine.root
        )
    except OSError:
        spill = TemporaryDirectory(prefix="termcode-subagent-")
    owned: Provider | None = None
    try:
        if provider_config is not None:
            owned = new_provider(provider_config)
        agent = parent.spawn_child(
            owned or parent.provider,
            child_tools(parent, definition, background=background),
            SessionContext(Path(spill.name).name, spill.name),
            context_window=effective_context_window(provider_config)
            if provider_config
            else parent.runtime.context_window,
            system_prompt=None
            if source in {"fork", "skill"}
            else definition.system_prompt,
            max_turns=definition.max_turns,
            permission_mode=definition.permission_mode
            if permission_mode is None
            else permission_mode,
            dont_ask=definition.dont_ask or parent.teammate is not None,
            query_source=source,
            label=label,
        )
    except BaseException:
        with suppress(Exception):
            spill.cleanup()
        if owned is not None:
            with suppress(Exception):
                await owned.aclose()
        raise
    return Child(agent, spill, owned)
