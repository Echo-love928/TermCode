import asyncio
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from termcode.worktree import (
    ExitAction,
    ExitOptions,
    Manager,
    WorktreeHasChangesError,
    flat_slug,
    random_agent_name,
    validate_slug,
)
from termcode.worktree import manager as manager_module
from termcode.worktree import session as session_module
from termcode.worktree.git import (
    _has_worktree_changes,
    _resolve_head_sha_from_fs,
    _run_git,
)
from termcode.worktree.slug import EPHEMERAL_PATTERN


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "测试")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(
        ".termcode/worktrees/\n.termcode/worktree_session.json\n.termcode/*.yaml\n"
        "node_modules/\n*.env\n",
        encoding="utf-8",
    )
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "initial")
    return tmp_path


@pytest.mark.parametrize("name", ["alice", "team/alice", "v1.0", "a_b", "a" * 64])
def test_valid_slug(name):
    validate_slug(name)
    assert flat_slug("team/alice") == "team+alice"
    assert EPHEMERAL_PATTERN.fullmatch(random_agent_name())


@pytest.mark.parametrize(
    "name",
    ["", "../etc", "..", "./x", "a//b", "/x", "a/", "a/b ", "a+b", "a;b", "a" * 65],
)
def test_invalid_slug(name):
    with pytest.raises(ValueError, match="拒绝"):
        validate_slug(name)


def test_construct_requires_repo_root(repo, tmp_path_factory):
    with pytest.raises(ValueError):
        Manager(tmp_path_factory.mktemp("non-git"))
    nested = repo / "nested"
    nested.mkdir()
    with pytest.raises(ValueError):
        Manager(nested)


def test_construct_warns_about_missing_ignore_without_rewriting(repo, capsys):
    Manager(repo)
    assert not capsys.readouterr().err
    ignore = repo / ".gitignore"
    ignore.write_text("*.env\n")
    Manager(repo)
    assert "Git 忽略规则" in capsys.readouterr().err
    assert ignore.read_text() == "*.env\n"


async def test_run_git_noninteractive_and_fail_closed(repo, monkeypatch):
    start = asyncio.create_subprocess_exec

    async def inspect(*args, **kwargs):
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert kwargs["env"]["GIT_ASKPASS"] == ""
        assert kwargs["stdin"] == asyncio.subprocess.DEVNULL
        return await start(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", inspect)
    head = await _run_git(repo, "rev-parse", "HEAD")
    assert not await _has_worktree_changes(repo, head)
    assert await _has_worktree_changes(repo, "invalid-sha")
    (repo / "extra").touch()
    assert await _has_worktree_changes(repo, head)


async def test_create_setup_and_fast_recovery(repo, monkeypatch):
    (repo / ".husky").mkdir()
    (repo / "node_modules").mkdir()
    (repo / "nested").mkdir()
    (repo / "nested" / "with space.env").write_text("FIXTURE=1", encoding="utf-8")
    (repo / ".worktreeinclude").write_text("*.env\n", encoding="utf-8")
    manager = Manager(repo)
    config = repo / ".termcode" / "settings.local.yaml"
    config.write_text("fixture: true", encoding="utf-8")
    wt = await manager.create("team/alice", "HEAD", manual=True)
    path = Path(wt.path)
    assert path == manager.worktree_dir / "team+alice"
    assert wt.branch == git(path, "branch", "--show-current") == "worktree-team+alice"
    assert (path / ".termcode" / config.name).read_bytes() == config.read_bytes()
    assert Path(git(path, "config", "core.hooksPath")) == repo / ".husky"
    with pytest.raises(RuntimeError):
        await _run_git(repo, "config", "--get", "core.hooksPath")
    if os.name != "nt":
        assert (path / "node_modules").is_symlink()
    assert (path / "nested" / "with space.env").read_text() == "FIXTURE=1"
    git(repo, "pack-refs", "--all")
    assert _resolve_head_sha_from_fs(path) == wt.head_commit
    with pytest.raises(ValueError, match="已存在"):
        await manager.create(wt.name, "HEAD", manual=False)

    async def no_git(*args):
        pytest.fail("快速恢复启动了 Git")

    monkeypatch.setattr(manager_module, "_run_git", no_git)
    manager.active.clear()
    assert await manager.create(wt.name, "HEAD", manual=False) == wt


async def test_session_restore_and_atomic_write(repo, monkeypatch):
    manager = Manager(repo)
    assert manager.current_session() is None
    wt = await manager.create("alice", "HEAD", manual=True)
    original = Path.cwd()
    session = await manager.enter(wt.name)
    assert Path.cwd() == original
    assert session.original_head_commit == wt.head_commit
    assert session.original_branch == "main"
    assert session.worktree_path == wt.path
    assert session.session_id
    assert Manager(repo).current_session() == session
    raw = manager.session_file.read_text()
    assert '"original_cwd"' in raw and '"worktree_path"' in raw

    def fail_replace(*args):
        raise OSError("fixture")

    with monkeypatch.context() as patch:
        patch.setattr(session_module.os, "replace", fail_replace)
        with pytest.raises(OSError):
            session_module.save_session(manager.session_file, None)
    assert manager.session_file.read_text() == raw

    git(repo, "worktree", "remove", "--force", wt.path)
    assert Manager(repo).current_session() is None
    assert manager.session_file.read_text() == "null"
    manager.session_file.write_text("{broken", encoding="utf-8")
    assert Manager(repo).current_session() is None
    assert manager.session_file.read_text() == "null"


async def test_exit_and_remove_protect_dirty_and_committed_changes(repo, monkeypatch):
    manager = Manager(repo)
    wt = await manager.create("alice", "HEAD", manual=True)
    session = await manager.enter(wt.name)
    path = Path(wt.path)
    (path / "dirty").touch()
    with pytest.raises(WorktreeHasChangesError):
        await manager.exit(wt.name, ExitAction.REMOVE, ExitOptions())
    assert manager.current_session() == session
    assert await asyncio.to_thread(path.is_dir)
    git(path, "add", "dirty")
    git(path, "commit", "-m", "worktree change")
    restored = Manager(repo)
    with pytest.raises(WorktreeHasChangesError):
        await restored.remove(wt.name, ExitOptions())
    monkeypatch.chdir(repo)
    report = await restored.exit(wt.name, ExitAction.REMOVE, ExitOptions(True))
    assert Path.cwd() == Path(session.original_cwd)
    assert report.removed and not await asyncio.to_thread(path.exists)
    assert not git(repo, "branch", "--list", wt.branch)
    assert restored.current_session() is None
    assert manager.session_file.read_text() == "null"


async def test_auto_cleanup_and_sweep(repo):
    manager = Manager(repo)
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/main", base)
    names = [f"agent-a{i:07x}" for i in range(7)]
    trees = [
        await manager.create(name, "HEAD", manual=(index == 0))
        for index, name in enumerate(names + ["other"])
    ]
    assert (await manager.auto_cleanup(names[0])).kept
    assert not (await manager.auto_cleanup(names[6])).kept
    (Path(trees[2].path) / "dirty").touch()
    git(Path(trees[3].path), "commit", "--allow-empty", "-m", "unpublished")
    await manager.enter(names[4])
    cutoff = datetime.now() - timedelta(hours=24)
    old = cutoff.timestamp() - 1000
    for wt in [*trees[:5], trees[-1]]:
        os.utime(wt.path, (old, old))
    restored = Manager(repo)
    assert await restored.sweep_stale(cutoff) == [names[1]]
    assert (await restored.auto_cleanup(names[2])).kept
    assert {wt.name for wt in restored.list()} == set(names) - {names[1], names[6]} | {
        "other"
    }


async def test_branch_collision_does_not_reset_existing_commit(repo):
    git(repo, "commit", "--allow-empty", "-m", "existing")
    git(repo, "branch", "worktree-existing")
    expected = git(repo, "rev-parse", "worktree-existing")
    manager = Manager(repo)
    with pytest.raises(RuntimeError):
        await manager.create("existing", "HEAD~", manual=True)
    assert git(repo, "rev-parse", "worktree-existing") == expected
    assert not (manager.worktree_dir / "existing").exists()
