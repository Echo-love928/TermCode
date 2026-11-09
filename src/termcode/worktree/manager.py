from __future__ import annotations

import asyncio
import json
import os
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .create import _perform_post_creation_setup, warn
from .git import (
    _has_worktree_changes,
    _linked_git_dir,
    _resolve_head_sha_from_fs,
    _run_git,
    git_env,
)
from .lifecycle import (
    AutoCleanupReport,
    ExitAction,
    ExitOptions,
    ExitReport,
    WorktreeHasChangesError,
)
from .session import WorktreeSession, clear_session, load_session, save_session
from .slug import EPHEMERAL_PATTERN, flat_slug, validate_slug

DEFAULT_SYMLINK_DIRS = ("node_modules", ".venv", "vendor")
_METADATA = "termcode-worktree.json"


@dataclass(frozen=True)
class Worktree:
    name: str
    path: str
    branch: str
    based_on: str
    head_commit: str
    created: datetime
    manual: bool


class Manager:
    def __init__(self, repo_root: str | Path) -> None:
        root = Path(repo_root).resolve()
        check = subprocess.run(
            ["git", "rev-parse", "--show-toplevel", "--git-common-dir"],
            cwd=root,
            env=git_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
        )
        lines = os.fsdecode(check.stdout).splitlines()
        if check.returncode or len(lines) != 2 or Path(lines[0]).resolve() != root:
            raise ValueError("当前目录不是 Git 仓库根目录")
        self.repo_root = str(root)
        self._common_dir = (root / lines[1]).resolve()
        self.worktree_dir = root / ".termcode" / "worktrees"
        self.session_file = self.worktree_dir.parent / "worktree_session.json"
        if (
            self.worktree_dir.resolve() != self.worktree_dir
            or self.session_file.is_symlink()
        ):
            raise ValueError("Worktree 存储路径不能使用符号链接")
        self.worktree_dir.mkdir(parents=True, exist_ok=True)
        self.symlink_dirs = DEFAULT_SYMLINK_DIRS
        self.lock = asyncio.Lock()
        self.active: dict[str, Worktree] = {}
        for path in self.worktree_dir.iterdir():
            if path.is_dir():
                recovered = self._recover(path)
                if recovered is not None:
                    self.active[recovered.name] = recovered
        self._session = self._restore_session()
        self._check_ignores()

    def _check_ignores(self) -> None:
        relative = (".termcode/worktrees/", ".termcode/worktree_session.json")
        check = subprocess.run(
            ["git", "check-ignore", "--", *relative],
            cwd=self.repo_root,
            env=git_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
        )
        if not set(relative).issubset(os.fsdecode(check.stdout).splitlines()):
            warn(
                "请在 Git 忽略规则中排除 "
                ".termcode/worktrees/ 和 .termcode/worktree_session.json"
            )

    def _restore_session(self) -> WorktreeSession | None:
        try:
            session = load_session(self.session_file)
            if session is None:
                return None
            wt = self.get(session.worktree_name)
            if wt is not None and session.worktree_path == wt.path:
                return session
            warn("会话对应的 Worktree 已失效，已清空会话")
        except (OSError, ValueError, TypeError) as exc:
            warn(f"Worktree 会话无效：{exc}")
        clear_session(self.session_file)
        return None

    def _recover(self, path: Path) -> Worktree | None:
        name = path.name.replace("+", "/")
        try:
            validate_slug(name)
            git_dir = _linked_git_dir(path, self._common_dir)
            head = _resolve_head_sha_from_fs(path)
            if not head:
                return None
            created = datetime.fromtimestamp(path.stat().st_mtime)
        except (OSError, ValueError, RuntimeError):
            return None
        branch = f"worktree-{path.name}"
        try:
            meta = json.loads((git_dir / _METADATA).read_text(encoding="utf-8"))
            if meta["name"] != name:
                return None
            based_on, base, manual = (
                meta["based_on"],
                meta["head_commit"],
                meta["manual"],
            )
            if not isinstance(based_on, str) or not isinstance(base, str):
                raise ValueError("Worktree 基线格式无效")
            if not isinstance(manual, bool):
                raise ValueError("Worktree 创建方式格式无效")
            created = datetime.fromisoformat(meta["created"])
        except (OSError, ValueError, KeyError, TypeError):
            # 缺少创建记录时无法用当前 HEAD 代替基线，否则重启后可能丢失提交。
            based_on, base, manual = "", head, True
            try:
                head_ref = (git_dir / "HEAD").read_text(encoding="ascii").strip()
            except (OSError, ValueError):
                return None
            if head_ref != f"ref: refs/heads/{branch}":
                return None
        return Worktree(name, str(path), branch, based_on, base, created, manual)

    def list(self) -> list[Worktree]:
        return sorted(self.active.values(), key=lambda wt: wt.name)

    def get(self, name: str) -> Worktree | None:
        return self.active.get(name)

    def current_session(self) -> WorktreeSession | None:
        return self._session

    def _require(self, name: str) -> Worktree:
        wt = self.get(name)
        if wt is None:
            raise ValueError(f"找不到 Worktree：{name}")
        return wt

    async def create(self, name: str, base_ref: str, manual: bool) -> Worktree:
        validate_slug(name)
        flat = flat_slug(name)
        path = self.worktree_dir / flat
        branch = f"worktree-{flat}"
        async with self.lock:
            if self.get(name) is not None:
                raise ValueError(f"Worktree 已存在：{name}")
            if path.exists() or path.is_symlink():
                wt = self._recover(path)
                if wt is None:
                    raise ValueError(f"已有目录无法恢复为 Worktree：{path}")
            else:
                wt = await self._create(name, path, branch, base_ref, manual)
            self.active[name] = wt
            return wt

    async def _create(
        self, name: str, path: Path, branch: str, base_ref: str, manual: bool
    ) -> Worktree:
        try:
            # 保留同名分支中的历史，避免把残留分支强制重置。
            await _run_git(
                self.repo_root,
                "worktree",
                "add",
                "-b",
                branch,
                "--",
                str(path),
                base_ref,
            )
            head = await _run_git(path, "rev-parse", "HEAD")
            created = datetime.now()
            git_dir = _linked_git_dir(path, self._common_dir)
            (git_dir / _METADATA).write_text(
                json.dumps(
                    {
                        "based_on": base_ref,
                        "name": name,
                        "head_commit": head,
                        "manual": manual,
                        "created": created.isoformat(),
                    }
                ),
                encoding="utf-8",
            )
            await _perform_post_creation_setup(
                Path(self.repo_root), path, self.symlink_dirs
            )
        except BaseException:
            # Git 可能已完成登记；恢复实际状态，让用户仍能查看及处理副本。
            recovered = self._recover(path)
            if recovered is not None:
                self.active[name] = recovered
            raise
        return Worktree(name, str(path), branch, base_ref, head, created, manual)

    async def enter(self, name: str) -> WorktreeSession:
        async with self.lock:
            wt = self._require(name)
            _linked_git_dir(Path(wt.path), self._common_dir)
            branch = await _run_git(self.repo_root, "rev-parse", "--abbrev-ref", "HEAD")
            head = await _run_git(self.repo_root, "rev-parse", "HEAD")
            original_cwd = (
                self._session.original_cwd if self._session else str(Path.cwd())
            )
            session = WorktreeSession(
                original_cwd, wt.path, name, branch, head, str(uuid4())
            )
            save_session(self.session_file, session)
            self._session = session
            return session

    async def _check_changes(self, wt: Worktree, opts: ExitOptions) -> None:
        _linked_git_dir(Path(wt.path), self._common_dir)
        if opts.discard_changes:
            return
        if not wt.based_on or await _has_worktree_changes(wt.path, wt.head_commit):
            raise WorktreeHasChangesError(
                f"Worktree 有未提交修改、新提交或状态无法确认：{wt.name}"
            )
        try:
            count = await _run_git(
                self.repo_root,
                "rev-list",
                "--count",
                f"{wt.head_commit}..refs/heads/{wt.branch}",
                "--",
            )
            if int(count) == 0:
                return
        except (OSError, RuntimeError, ValueError):
            pass
        raise WorktreeHasChangesError(f"Worktree 分支有新提交或状态无法确认：{wt.name}")

    def _restore_cwd(self) -> None:
        if self._session is None:
            return
        with suppress(OSError):
            if os.getcwd() != self._session.original_cwd:
                os.chdir(self._session.original_cwd)

    def _clear_session(self) -> None:
        self._restore_cwd()
        clear_session(self.session_file)
        self._session = None

    async def _remove(self, wt: Worktree) -> None:
        if self._session and self._session.worktree_name == wt.name:
            self._restore_cwd()
        removal = asyncio.create_task(self._remove_files_and_branch(wt))
        try:
            await asyncio.shield(removal)
        except asyncio.CancelledError:
            await asyncio.gather(removal, return_exceptions=True)
            raise

    async def _remove_files_and_branch(self, wt: Worktree) -> None:
        try:
            await _run_git(
                self.repo_root, "worktree", "remove", "--force", "--", wt.path
            )
        finally:
            if not await asyncio.to_thread(Path(wt.path).exists):
                del self.active[wt.name]
                if self._session and self._session.worktree_name == wt.name:
                    self._clear_session()
        await asyncio.sleep(0.1)
        await _run_git(self.repo_root, "branch", "-D", "--", wt.branch)

    async def exit(
        self, name: str, action: ExitAction, opts: ExitOptions
    ) -> ExitReport:
        async with self.lock:
            if self._session is None or self._session.worktree_name != name:
                raise ValueError("只能退出当前 Worktree 会话")
            wt = self._require(name)
            if action == ExitAction.REMOVE:
                await self._check_changes(wt, opts)
                await self._remove(wt)
            else:
                self._clear_session()
            return ExitReport(action == ExitAction.REMOVE, wt.path, wt.branch)

    async def remove(self, name: str, opts: ExitOptions) -> None:
        async with self.lock:
            wt = self._require(name)
            await self._check_changes(wt, opts)
            await self._remove(wt)

    async def auto_cleanup(self, name: str) -> AutoCleanupReport:
        async with self.lock:
            wt = self._require(name)
            kept = AutoCleanupReport(True, wt.path, wt.branch)
            if wt.manual or (self._session and self._session.worktree_name == name):
                return kept
            try:
                await self._check_changes(wt, ExitOptions())
                await self._remove(wt)
            except WorktreeHasChangesError:
                return kept
            return AutoCleanupReport(False)

    async def sweep_stale(self, cutoff: datetime) -> list[str]:
        removed = []
        for wt in self.list():
            async with self.lock:
                if (
                    self.get(wt.name) is not wt
                    or wt.manual
                    or not EPHEMERAL_PATTERN.fullmatch(wt.name)
                    or (self._session and self._session.worktree_name == wt.name)
                ):
                    continue
                try:
                    stat = await asyncio.to_thread(Path(wt.path).stat)
                    if stat.st_mtime > cutoff.timestamp():
                        continue
                    await self._check_changes(wt, ExitOptions())
                    if await _run_git(
                        wt.path,
                        "rev-list",
                        "--max-count=1",
                        "HEAD",
                        "--not",
                        "--remotes",
                    ):
                        continue
                    await self._remove(wt)
                except (OSError, RuntimeError, ValueError) as exc:
                    if not isinstance(exc, WorktreeHasChangesError):
                        warn(f"无法清理过期 Worktree {wt.name}：{exc}")
                    continue
                removed.append(wt.name)
        return removed
