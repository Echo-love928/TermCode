"""状态冻结、快照隔离与真实线程并发。"""

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from termcode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    new_session_context,
)


def test_new_session_context(tmp_path):
    first = new_session_context(str(tmp_path))
    second = new_session_context(str(tmp_path))
    assert re.fullmatch(r"\d+-[0-9a-f]{8}", first.session_id)
    assert first.session_id != second.session_id
    assert Path(first.spill_dir).is_dir()


def test_new_session_context_rand_fail_fallback(tmp_path, monkeypatch):
    def fail(_):
        raise OSError("无随机源")

    monkeypatch.setattr("termcode.compact.state.secrets.token_hex", fail)
    assert re.fullmatch(
        r"\d+-[0-9a-f]{8}", new_session_context(str(tmp_path)).session_id
    )


def test_decide_once_freeze_kept_replaced_and_skip():
    state = ContentReplacementState()
    preview = "稳定预览" * 30
    assert state.decide_once("keep", "原文", lambda: ("kept", "")) == "原文"
    assert state.decide_once("keep", "原文", lambda: ("replaced", preview)) == "原文"
    assert "keep" in state._seen_ids and "keep" not in state._replacements
    assert state.decide_once("big", "大原文", lambda: ("replaced", preview)) is preview
    assert state.decide_once("big", "大原文", lambda: ("kept", "")) is preview
    state.decide_once("retry", "原文", lambda: ("skip", ""))
    assert "retry" not in state._seen_ids
    assert state.decide_once("retry", "原文", lambda: ("replaced", preview)) is preview


def test_recovery_state_snapshot_order(tmp_path):
    state = RecoveryState()
    state.record_file(str(tmp_path / "old"), "old")
    state.record_file(str(tmp_path / "new"), "new")
    state.record_file(str(tmp_path / "old"), "updated")
    snapshot = state.snapshot()
    assert [r.content for r in snapshot] == ["updated", "new"]
    snapshot[0].content = "corrupt"
    snapshot.clear()
    assert state.snapshot()[0].content == "updated"


def test_recovery_state_concurrent(tmp_path):
    state = RecoveryState()
    barrier = threading.Barrier(50)

    def work(i):
        barrier.wait(timeout=15)
        state.record_file(str(tmp_path / str(i)), str(i))
        assert all(Path(r.path).is_absolute() for r in state.snapshot())

    with ThreadPoolExecutor(max_workers=50) as pool:
        list(pool.map(work, range(50)))
    assert len(state.snapshot()) == 50


def test_auto_tracking_consecutive_budget():
    state = CompactCircuitBreaker()
    counts = []
    for success in (False, False, True, False, False, False):
        state.record_success() if success else state.record_failure()
        counts.append(state.consecutive_failures)
    assert counts == [1, 2, 0, 1, 2, 3]
    assert state.tripped()
    state.record_success()
    assert not state.tripped()


def test_auto_tracking_concurrent():
    state = CompactCircuitBreaker()

    def write(_):
        for _ in range(50):
            state.record_failure()
            state.tripped()

    with ThreadPoolExecutor(max_workers=20) as pool:
        list(pool.map(write, range(20)))
    assert state.consecutive_failures == 1000

    def mixed(i):
        state.record_success() if i % 2 else state.record_failure()
        assert state.consecutive_failures >= 0
        state.tripped()

    with ThreadPoolExecutor(max_workers=20) as pool:
        list(pool.map(mixed, range(500)))
