import asyncio
import fnmatch
import os
import shutil
import sys
from pathlib import Path

from .git import _run_git


def warn(message: str) -> None:
    print(f"Worktree：{message}", file=sys.stderr)


def _copy_file(source: Path, target: Path, root: Path) -> None:
    if (
        source.is_symlink()
        or not source.is_file()
        or target.exists()
        or target.is_symlink()
    ):
        return
    if not target.parent.resolve().is_relative_to(root):
        raise ValueError(f"复制目标超出 Worktree：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target, follow_symlinks=False)


async def _setup_git_hooks(repo: Path, worktree: Path) -> None:
    if (repo / ".husky").is_dir():
        hooks = repo / ".husky"
    else:
        try:
            configured = await _run_git(repo, "config", "--get", "core.hooksPath")
        except RuntimeError:
            return
        if not configured:
            return
        hooks = repo / await asyncio.to_thread(Path(configured).expanduser)
    try:
        enabled = await _run_git(
            repo, "config", "--local", "--bool", "--get", "extensions.worktreeConfig"
        )
    except RuntimeError:
        enabled = ""
    if enabled != "true":
        try:
            bare = await _run_git(
                repo, "config", "--local", "--get-regexp", r"^core\.(bare|worktree)$"
            )
        except RuntimeError:
            bare = ""
        if bare not in ("", "core.bare false"):
            raise ValueError("当前仓库需先迁移 core.bare/core.worktree 配置")
        await _run_git(repo, "config", "--local", "extensions.worktreeConfig", "true")
    await _run_git(
        worktree, "config", "--worktree", "core.hooksPath", str(hooks.resolve())
    )


async def _copy_included_ignored(repo: Path, worktree: Path) -> None:
    include = repo / ".worktreeinclude"
    if not include.is_file():
        return
    raw_patterns = await asyncio.to_thread(include.read_text, encoding="utf-8")
    patterns = [
        line
        for raw in raw_patterns.splitlines()
        if (line := raw.strip()) and not line.startswith("#")
    ]
    if not patterns:
        return
    # 逐文件列出，确保目录中的忽略文件也能按模式匹配。
    ignored = (
        await _run_git(
            repo,
            "ls-files",
            "-z",
            "--others",
            "--ignored",
            "--exclude-standard",
            "--",
            ".",
            ":(exclude).termcode/worktrees",
            ":(exclude).termcode/worktree_session.json",
        )
    ).split("\0")
    matched = set()
    for relative in filter(None, ignored):
        matching = [
            pattern for pattern in patterns if fnmatch.fnmatchcase(relative, pattern)
        ]
        if not matching:
            continue
        matched.update(matching)
        source = repo / relative
        try:
            if not source.resolve().is_relative_to(repo):
                raise ValueError(f"源文件超出仓库：{relative}")
            _copy_file(source, worktree / relative, worktree)
        except (OSError, ValueError, RuntimeError) as exc:
            warn(f"无法复制忽略文件 {relative}：{exc}")
    for pattern in dict.fromkeys(patterns):
        if pattern not in matched:
            warn(f".worktreeinclude 未匹配忽略文件：{pattern}")


async def _perform_post_creation_setup(
    repo: Path, worktree: Path, symlink_dirs: tuple[str, ...]
) -> None:
    for name in ("config.yaml", "settings.local.yaml"):
        relative = Path(".termcode") / name
        try:
            _copy_file(repo / relative, worktree / relative, worktree)
        except (OSError, ValueError, RuntimeError) as exc:
            warn(f"无法复制本地配置 {name}：{exc}")
    try:
        await _setup_git_hooks(repo, worktree)
    except (OSError, ValueError, RuntimeError) as exc:
        warn(f"无法设置 Git Hooks：{exc}")
    for name in symlink_dirs:
        source, target = repo / name, worktree / name
        try:
            if not target.parent.resolve().is_relative_to(worktree):
                raise ValueError(f"链接目标超出 Worktree：{target}")
            if source.is_dir() and not (target.exists() or target.is_symlink()):
                os.symlink(source, target, target_is_directory=True)
        except (OSError, ValueError, RuntimeError) as exc:
            warn(f"无法链接目录 {name}：{exc}")
    try:
        await _copy_included_ignored(repo, worktree)
    except (OSError, ValueError, RuntimeError) as exc:
        warn(f"无法读取 Worktree 包含文件：{exc}")
