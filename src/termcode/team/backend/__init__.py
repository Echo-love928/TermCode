from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from ..types import BackendType

if TYPE_CHECKING:
    from termcode.agent import Agent
    from termcode.conversation import Conversation
    from termcode.task import Manager


@dataclass
class SpawnRequest:
    team_name: str
    member_name: str
    agent_id: str
    worktree_path: str
    session_dir: str
    agent_type: str
    model: str
    initial_prompt: str
    plan_mode_required: bool
    project_root: str
    provider_name: str = ""
    sub_agent: Agent | None = None
    conv: Conversation | None = None
    cleanup: Callable[[], Awaitable[None]] | None = None
    restore: Callable[[], Conversation] | None = None


class Backend(Protocol):
    def type(self) -> BackendType: ...
    async def spawn(self, req: SpawnRequest) -> tuple[str, str]: ...
    async def wake(self, pane_id: str, agent_id: str) -> None: ...
    async def kill(self, pane_id: str, agent_id: str) -> None: ...
    async def alive(self, pane_id: str, agent_id: str) -> bool: ...


def new_backend(kind: BackendType, task_mgr: Manager) -> Backend:
    from .inprocess import InProcessBackend
    from .iterm2 import Iterm2Backend
    from .tmux import TmuxBackend

    match kind:
        case BackendType.TMUX:
            return TmuxBackend()
        case BackendType.ITERM2:
            return Iterm2Backend()
        case BackendType.IN_PROCESS:
            return InProcessBackend(task_mgr)
    raise ValueError(f"未知团队后端：{kind}")
