import asyncio
import os
import re
from pathlib import Path

_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


def git_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        env.pop(key, None)
    return env | {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""}


async def _run_git(work_dir: str | Path, *args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=work_dir,
        env=git_env(),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    communication = asyncio.create_task(proc.communicate())
    try:
        stdout, stderr = await asyncio.shield(communication)
    except asyncio.CancelledError:
        # 等 Git 写完索引及引用，再让上层检查实际状态。
        await communication
        raise
    if proc.returncode:
        detail = stderr.decode(errors="replace").strip()
        raise RuntimeError(f"Git {args[0]} 执行失败：{detail or proc.returncode}")
    return os.fsdecode(stdout).rstrip("\r\n")


async def _has_worktree_changes(wt_path: str | Path, base_commit: str) -> bool:
    try:
        if await _run_git(wt_path, "status", "--porcelain", "--untracked-files=all"):
            return True
        if not base_commit:
            return True
        count = await _run_git(
            wt_path, "rev-list", "--count", f"{base_commit}..HEAD", "--"
        )
        return int(count) != 0
    except (OSError, RuntimeError, ValueError):
        return True


def _linked_git_dir(path: Path, common_dir: Path | None = None) -> Path:
    pointer = path / ".git"
    if path.is_symlink() or pointer.is_symlink() or not pointer.is_file():
        raise ValueError("无效的 Worktree Git 指针")
    prefix, sep, target = pointer.read_text(encoding="utf-8").strip().partition(": ")
    if not sep or prefix != "gitdir":
        raise ValueError("无效的 Worktree Git 指针")
    git_dir = (path / target).resolve(strict=True)
    if common_dir is not None and git_dir.parent != common_dir / "worktrees":
        raise ValueError("Worktree 未登记在当前 Git 仓库")
    common = git_dir / (git_dir / "commondir").read_text(encoding="utf-8").strip()
    common = common.resolve(strict=True)
    backlink = git_dir / (git_dir / "gitdir").read_text(encoding="utf-8").strip()
    if (
        git_dir.parent != common / "worktrees"
        or backlink.resolve(strict=True) != pointer.resolve(strict=True)
        or (common_dir is not None and common != common_dir)
    ):
        raise ValueError("Worktree 未登记在当前 Git 仓库")
    return git_dir


def _resolve_head_sha_from_fs(wt_path: str | Path) -> str | None:
    try:
        git_dir = _linked_git_dir(Path(wt_path))
        head = (git_dir / "HEAD").read_text(encoding="ascii").strip()
        if head.startswith("ref: refs/heads/"):
            ref = head.removeprefix("ref: ")
            if any(part in {"", ".", ".."} for part in ref.split("/")) or "\\" in ref:
                return None
            common = git_dir.parent.parent
            try:
                head = (common / ref).read_text(encoding="ascii").strip()
            except FileNotFoundError:
                head = next(
                    (
                        line.split(" ", 1)[0]
                        for line in (common / "packed-refs")
                        .read_text(encoding="utf-8")
                        .splitlines()
                        if line.endswith(" " + ref)
                    ),
                    "",
                )
        return head if _SHA.fullmatch(head) else None
    except (OSError, ValueError, RuntimeError):
        return None
