from termcode.task import Manager

from ..types import BackendType
from . import SpawnRequest


class InProcessBackend:
    def __init__(self, task_mgr: Manager) -> None:
        self.task_mgr = task_mgr

    def type(self) -> BackendType:
        return BackendType.IN_PROCESS

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]:
        if req.sub_agent is None or req.conv is None:
            raise ValueError("进程内队员缺少 Agent 或会话")
        agent_id = await self.task_mgr.launch(
            req.sub_agent,
            req.conv,
            req.member_name,
            req.initial_prompt,
            cleanup=req.cleanup,
            id_=req.agent_id,
            cwd=req.worktree_path,
            restore=req.restore,
        )
        return "", agent_id

    async def wake(self, pane_id: str, agent_id: str) -> None:
        pass

    async def kill(self, pane_id: str, agent_id: str) -> None:
        await self.task_mgr.remove(agent_id)

    async def alive(self, pane_id: str, agent_id: str) -> bool:
        return self.task_mgr.get(agent_id) is not None
