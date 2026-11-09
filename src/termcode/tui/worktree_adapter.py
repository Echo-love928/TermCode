from collections.abc import Callable

from termcode.command.ui import WorktreeSummary
from termcode.worktree import ExitAction, ExitOptions, Manager


class WorktreeAdapter:
    def __init__(self, manager: Manager, set_active_cwd: Callable[[str], None]) -> None:
        self.manager = manager
        self.set_active_cwd = set_active_cwd

    async def create(self, name: str) -> tuple[str, str]:
        wt = await self.manager.create(name, "HEAD", manual=True)
        return wt.path, wt.branch

    def list(self) -> list[WorktreeSummary]:
        session = self.manager.current_session()
        return [
            WorktreeSummary(
                wt.name,
                wt.path,
                wt.branch,
                session is not None and wt.name == session.worktree_name,
                wt.manual,
            )
            for wt in self.manager.list()
        ]

    async def enter(self, name: str) -> str:
        session = await self.manager.enter(name)
        self.set_active_cwd(session.worktree_path)
        return session.worktree_path

    def _sync_cwd(self) -> None:
        session = self.manager.current_session()
        self.set_active_cwd(session.worktree_path if session else "")

    async def exit(self, action: str, discard: bool) -> bool:
        session = self.manager.current_session()
        if session is None:
            raise ValueError("当前没有活跃的 Worktree 会话")
        try:
            report = await self.manager.exit(
                session.worktree_name, ExitAction(action), ExitOptions(discard)
            )
            return report.removed
        finally:
            self._sync_cwd()

    async def remove(self, name: str, discard: bool) -> None:
        try:
            await self.manager.remove(name, ExitOptions(discard))
        finally:
            self._sync_cwd()
