import asyncio
import sys

from . import SpawnRequest


class BackendError(RuntimeError):
    pass


async def run(*cmd: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=10)
    except (TimeoutError, asyncio.CancelledError):
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    if proc.returncode:
        raise BackendError(err.decode(errors="replace").strip() or f"{cmd[0]} 执行失败")
    return out.decode(errors="replace").strip()


def build_member_cmd(req: SpawnRequest) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "termcode",
        "--team-member",
        "--team",
        req.team_name,
        "--member",
        req.member_name,
        "--agent-id",
        req.agent_id,
        "--session-dir",
        req.session_dir,
        "--worktree",
        req.worktree_path,
        "--project-root",
        req.project_root,
    ]
    for flag, value in (
        ("--agent-type", req.agent_type),
        ("--model", req.model),
        ("--provider", req.provider_name),
    ):
        if value:
            cmd.extend((flag, value))
    if req.plan_mode_required:
        cmd.append("--plan-mode")
    return cmd
