from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from termcode.agent import Agent
    from termcode.config import ProviderConfig
    from termcode.conversation import Conversation
    from termcode.subagent import Definition
    from termcode.team.mailbox import Box
    from termcode.team.types import BackendType


class InProcessTeammateNoSpawnError(ValueError):
    pass


@dataclass
class TeammateContext:
    team_name: str
    member_name: str
    agent_id: str
    backend_type: BackendType
    mailbox: Box
    shutdown_requested: bool = False


@dataclass(frozen=True)
class TeamSpawnRequest:
    team_name: str
    member_name: str
    prompt: str
    definition: Definition
    model: str
    parent: Agent
    conversation: Conversation
    provider_config: ProviderConfig | None = None
    provider_name: str = ""
    plan_mode_required: bool = False


class TeamHook(Protocol):
    fork_enabled: bool

    async def spawn_teammate(self, req: TeamSpawnRequest) -> dict[str, str]: ...
