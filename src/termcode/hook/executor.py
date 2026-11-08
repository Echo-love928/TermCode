"""执行 Hook 动作，并把动作失败与主动拦截分开。"""

import asyncio
import json
import os
import signal
import sys
from contextlib import suppress
from dataclasses import dataclass

import httpx2

from termcode.hook.rule import HttpAction, Payload, PromptAction, Rule, ShellAction


@dataclass(frozen=True)
class ExecutionResult:
    blocked: bool = False
    reason: str = ""
    prompt: str = ""
    err: Exception | None = None


class Executor:
    def __init__(self) -> None:
        self._http_client: httpx2.AsyncClient | None = None
        self._stderr = sys.stderr

    async def aclose(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()

    async def run(
        self, rule: Rule, payload: Payload, *, blocking: bool
    ) -> ExecutionResult:
        action = rule.action
        try:
            if isinstance(action, ShellAction):
                return await self._run_shell(action, payload, blocking, rule.timeout_s)
            if isinstance(action, HttpAction):
                return await self._run_http(action, payload, blocking, rule.timeout_s)
            if isinstance(action, PromptAction):
                return ExecutionResult(prompt=action.text)
            print(
                f"[hook subagent] not yet implemented, skipped: {action.agent_name}",
                file=self._stderr,
            )
            return ExecutionResult()
        except TimeoutError:
            return ExecutionResult(
                err=TimeoutError(f"timed out after {rule.timeout_s:g}s")
            )
        except Exception as exc:
            return ExecutionResult(err=exc)

    async def _run_shell(
        self, action: ShellAction, payload: Payload, blocking: bool, timeout_s: float
    ) -> ExecutionResult:
        data = _marshal_sorted(payload) + b"\n"
        spawn = asyncio.create_task(
            asyncio.create_subprocess_shell(
                action.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=payload.get("cwd"),
                start_new_session=os.name != "nt",
            )
        )
        try:
            proc = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            # 即使在创建时取消，也先拿到进程句柄再清理整组。
            with suppress(Exception):
                proc = await spawn
                await _kill_shell(proc)
                await proc.communicate()
            raise
        io = asyncio.create_task(proc.communicate(data))
        try:
            async with asyncio.timeout(timeout_s):
                stdout, stderr = await asyncio.shield(io)
        except (TimeoutError, asyncio.CancelledError):
            # 子进程也可能持有管道，终止整组后再回收通信任务。
            await _kill_shell(proc)
            await io
            raise
        if blocking and proc.returncode == 2:
            reason = (stderr or stdout).decode(errors="replace").rstrip("\r\n")
            return ExecutionResult(blocked=True, reason=reason)
        if proc.returncode:
            reason = stderr.decode(errors="replace").strip()
            return ExecutionResult(
                err=RuntimeError(f"exit {proc.returncode}: {reason}")
            )
        if stderr:
            self._stderr.write(stderr.decode(errors="replace"))
            self._stderr.flush()
        return ExecutionResult()

    async def _run_http(
        self, action: HttpAction, payload: Payload, blocking: bool, timeout_s: float
    ) -> ExecutionResult:
        headers = httpx2.Headers(action.headers)
        if action.body is None:
            body = _marshal_sorted(payload)
            headers.setdefault("Content-Type", "application/json")
        else:
            body = action.body.format_map(payload).encode()
        if self._http_client is None:
            self._http_client = httpx2.AsyncClient()
        async with asyncio.timeout(timeout_s):
            response = await self._http_client.request(
                action.method,
                action.url,
                content=body,
                headers=headers,
                timeout=timeout_s,
            )
        response.raise_for_status()
        if blocking:
            data = response.json()
            if isinstance(data, dict) and data.get("decision") == "block":
                reason = data.get("reason")
                if isinstance(reason, str):
                    return ExecutionResult(blocked=True, reason=reason)
        return ExecutionResult()


def _marshal_sorted(payload: Payload) -> bytes:
    return json.dumps(payload, sort_keys=True).encode()


async def _kill_shell(proc: asyncio.subprocess.Process) -> None:
    if sys.platform != "win32":
        with suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        return
    from termcode.tool.windows_process import kill_tree

    try:
        await asyncio.to_thread(kill_tree, proc.pid)
    finally:
        if proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.kill()
