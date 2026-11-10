import os
import re

from ..types import BackendType
from . import SpawnRequest
from .process import BackendError, build_member_cmd, run


class TmuxBackend:
    def type(self) -> BackendType:
        return BackendType.TMUX

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]:
        if os.environ.get("TMUX"):
            args = ["split-window", "-h"]
            if pane := os.environ.get("TMUX_PANE"):
                args.extend(("-t", pane))
        else:
            args = ["new-session", "-d", "-s", f"termcode-{req.agent_id}"]
        for name in ("HOME", "PYTHONPATH"):
            if name in os.environ:
                args.extend(("-e", f"{name}={os.environ[name]}"))
        pane = await run(
            "tmux",
            *args,
            "-P",
            "-F",
            "#{pane_id}",
            "-c",
            req.project_root,
            "--",
            *build_member_cmd(req),
        )
        if not re.fullmatch(r"%[0-9]+", pane):
            raise BackendError(f"tmux 未返回有效的窗格 ID：{pane}")
        try:
            await run(
                "tmux",
                "set-option",
                "-p",
                "-t",
                pane,
                "@termcode-agent-id",
                req.agent_id,
            )
        except BackendError:
            try:
                await run("tmux", "kill-pane", "-t", pane)
            except BackendError:
                pass
            raise
        return pane, req.agent_id

    async def wake(self, pane_id: str, agent_id: str) -> None:
        if not await self.alive(pane_id, agent_id):
            raise BackendError(f"队员窗格 {pane_id} 已退出")
        await run("tmux", "send-keys", "-t", pane_id, "Enter")

    async def kill(self, pane_id: str, agent_id: str) -> None:
        if (await self._pane_state(pane_id)).partition(" ")[0] != agent_id:
            return
        try:
            await run("tmux", "kill-pane", "-t", pane_id)
        except BackendError as exc:
            if not self._missing(exc):
                raise

    async def alive(self, pane_id: str, agent_id: str) -> bool:
        return await self._pane_state(pane_id) == f"{agent_id} 0"

    async def _pane_state(self, pane_id: str) -> str:
        if not re.fullmatch(r"%[0-9]+", pane_id):
            return ""
        try:
            state = await run(
                "tmux",
                "display-message",
                "-p",
                "-t",
                pane_id,
                "#{@termcode-agent-id} #{pane_dead}",
            )
        except BackendError as exc:
            if not self._missing(exc):
                raise
            return ""
        return state

    @staticmethod
    def _missing(error: BackendError) -> bool:
        return any(
            message in str(error).lower()
            for message in ("can't find pane", "no server running", "no such file")
        )
