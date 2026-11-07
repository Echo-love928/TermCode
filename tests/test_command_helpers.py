"""会话重置和文件名查询辅助接口。"""

from pathlib import Path

import pytest

from termcode.agent import SessionRuntime
from termcode.compact import new_session_context
from termcode.memory import Manager
from termcode.session import Writer
from termcode.tool import Registry, new_default_registry


@pytest.mark.parametrize(
    "names",
    [
        [],
        ["MEMORY.md"],
        ["z.md", "a.md", "MEMORY.md"],
        ["a.md", "ignore.txt", "data.json"],
    ],
)
def test_list_files(tmp_path, names):
    manager = Manager(str(tmp_path / "project"), str(tmp_path / "user"))
    if names:
        manager.project_store.ensure_dir()
        for name in names:
            (manager.project_store.directory / name).write_text(
                "正文", encoding="utf-8"
            )
        (manager.project_store.directory / "directory.md").mkdir()
    assert manager.list_files() == (sorted(n for n in names if n.endswith(".md")), [])
    assert not manager.user_store.directory.exists()


def test_list_files_permission_error_is_warning(tmp_path, monkeypatch, caplog):
    manager = Manager(str(tmp_path / "project"), str(tmp_path / "user"))

    def denied(path):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "iterdir", denied)
    assert manager.list_files() == ([], [])
    assert "无法列出记忆文件" in caplog.text


def test_writer_path(tmp_path):
    with Writer(str(tmp_path / "session")) as writer:
        assert Path(writer.path).is_absolute()
        assert Path(writer.path).is_file()


def test_reset_runtime(tmp_path):
    runtime = SessionRuntime(session=new_session_context(str(tmp_path)))
    previous = runtime.replacement, runtime.recovery, runtime.auto_tracking
    runtime.usage_anchor = 42
    runtime.anchor_msg_len = 3
    runtime.turn_count = 2
    runtime.auto_tracking.record_failure()
    runtime.recovery.record_file(str(tmp_path / "read.py"), "old")
    lock = runtime.run_lock
    context = new_session_context(str(tmp_path))
    runtime.reset_for_new_session(context)
    assert runtime.session is context and runtime.run_lock is lock
    assert runtime.context_window == 200000
    assert (runtime.usage_anchor, runtime.anchor_msg_len, runtime.turn_count) == (
        0,
        0,
        0,
    )
    assert runtime.recovery.snapshot() == []
    assert runtime.auto_tracking.consecutive_failures == 0
    assert all(
        a is not b
        for a, b in zip(
            previous,
            (runtime.replacement, runtime.recovery, runtime.auto_tracking),
            strict=True,
        )
    )


async def test_reset_blocks_when_runtime_locked(tmp_path):
    runtime = SessionRuntime(session=new_session_context(str(tmp_path)))
    previous = runtime.session
    async with runtime.run_lock:
        with pytest.raises(RuntimeError, match="等待"):
            runtime.reset_for_new_session(new_session_context(str(tmp_path)))
    assert runtime.session is previous


def test_tool_count():
    assert Registry().count() == 0
    reg = new_default_registry()
    assert reg.count() == 6 == len(reg.definitions())
