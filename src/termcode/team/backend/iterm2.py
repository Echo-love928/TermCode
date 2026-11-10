import json
import os
import re
import shlex
from contextlib import suppress

from ..types import BackendType
from . import SpawnRequest
from .process import BackendError, build_member_cmd, run

PANE_ID = re.compile(r"[a-zA-Z0-9_:%][a-zA-Z0-9_.:%-]*")


class Iterm2Backend:
    def type(self) -> BackendType:
        return BackendType.ITERM2

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]:
        split = ["it2", "session", "split", "--vertical"]
        if parent := os.environ.get("ITERM_SESSION_ID"):
            split.extend(("--session", parent))
        output = await run(*split)
        prefix = "Created new pane: "
        pane = output.removeprefix(prefix).strip()
        if not output.startswith(prefix) or not PANE_ID.fullmatch(pane):
            raise BackendError(f"iTerm2 未返回有效的窗格 ID：{output}")
        env = [
            f"{key}={os.environ[key]}"
            for key in ("HOME", "PYTHONPATH")
            if key in os.environ
        ]
        # iTerm2 自带的 it2 先分屏，再把启动命令发给新窗格的 shell。
        command = shlex.join(["exec", "env", *env, *build_member_cmd(req)])
        try:
            await run("it2", "session", "run", "--session", pane, command)
        except (OSError, RuntimeError):
            with suppress(OSError, RuntimeError):
                await self.kill(pane, req.agent_id)
            raise
        return pane, req.agent_id

    async def wake(self, pane_id: str, agent_id: str) -> None:
        if not await self.alive(pane_id, agent_id):
            raise BackendError(f"队员窗格 {pane_id} 已退出")
        await run("it2", "session", "send", "--session", pane_id, "\r")

    async def kill(self, pane_id: str, agent_id: str) -> None:
        if await self.alive(pane_id, agent_id):
            await run("it2", "session", "close", "--session", pane_id, "--force")

    async def alive(self, pane_id: str, agent_id: str) -> bool:
        if not PANE_ID.fullmatch(pane_id):
            return False
        output = await run("it2", "session", "list", "--json")
        try:
            sessions = json.loads(output)
            return any(session["id"] == pane_id for session in sessions)
        except (ValueError, TypeError, KeyError) as exc:
            raise BackendError("无法解析 iTerm2 窗格列表") from exc
