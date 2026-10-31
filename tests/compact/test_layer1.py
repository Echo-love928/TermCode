"""落盘阈值、冻结决策、失败重试与并发幂等。"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from termcode.compact import ContentReplacementState, layer1, new_session_context
from termcode.llm import Message, ToolResult


def tool_msg(*contents):
    return [
        Message(
            "tool", tool_results=[ToolResult(str(i), c) for i, c in enumerate(contents)]
        )
    ]


def test_spill_single_idempotent(tmp_path):
    session = new_session_context(str(tmp_path))
    layer1.spill_single(session, "a", "你好")
    path = Path(session.spill_dir) / "a"
    stamp = path.stat().st_mtime_ns
    layer1.spill_single(session, "a", "不应覆盖")
    assert path.stat().st_mtime_ns == stamp
    assert path.read_bytes() == "你好".encode()


@pytest.mark.parametrize(
    "content",
    ["x" * 60000, ("中文\n" * 10000), "中" * 20000],
    ids=["ascii", "chinese-lines", "chinese-long-line"],
)
def test_offload_single_result(tmp_path, content):
    session = new_session_context(str(tmp_path))
    msgs = tool_msg(content)
    out = layer1.offload_and_snip(msgs, ContentReplacementState(), session)
    preview = out[0].tool_results[0].content
    assert f"original size: {len(content.encode())}" in preview
    assert "[saved to]" in preview and session.spill_dir in preview
    assert "文件读取工具" in preview and "不要凭头部预览猜测" in preview
    head = preview.split("[head preview]\n", 1)[1].rsplit("\n完整内容", 1)[0]
    assert len(head.encode()) <= 2048 and len(head.splitlines()) <= 20
    assert (Path(session.spill_dir) / "0").read_bytes() == content.encode()
    assert msgs[0].tool_results[0].content == content


@pytest.mark.parametrize(
    "sizes,expected",
    [([80000] * 3, 3), ([50000] * 5, 1), ([50000] * 4, 0), ([40000] * 6, 1)],
)
def test_offload_aggregate(tmp_path, sizes, expected):
    session = new_session_context(str(tmp_path))
    out = layer1.offload_and_snip(
        tool_msg(*("x" * size for size in sizes)), ContentReplacementState(), session
    )
    assert len(list(Path(session.spill_dir).iterdir())) == expected
    assert sum(len(r.content.encode()) for r in out[0].tool_results) < 205000


def test_offload_decision_freeze_and_preview_identity(tmp_path, monkeypatch):
    session = new_session_context(str(tmp_path))
    state = ContentReplacementState()
    original = tool_msg("x" * 60000, "small")
    original_spill = layer1.spill_single
    original_preview = layer1.build_preview
    first = layer1.offload_and_snip(original, state, session)
    preview = first[0].tool_results[0].content

    def fail(*_):
        raise AssertionError("冻结后不应重新落盘或生成预览")

    monkeypatch.setattr(layer1, "spill_single", fail)
    monkeypatch.setattr(layer1, "build_preview", fail)
    for _ in range(6):
        second = layer1.offload_and_snip(first, state, session)
        assert second[0].tool_results[0].content is preview
        assert second[0].tool_results[1].content == "small"
    # 保留项不会因为之后同一消息新增结果而翻转。
    monkeypatch.setattr(layer1, "spill_single", original_spill)
    monkeypatch.setattr(layer1, "build_preview", original_preview)
    extended = tool_msg("x" * 60000, "small", *("y" * 50000 for _ in range(5)))
    assert (
        layer1.offload_and_snip(extended, state, session)[0].tool_results[1].content
        == "small"
    )


def test_offload_spill_failure_retryable(tmp_path, monkeypatch):
    session = new_session_context(str(tmp_path))
    state = ContentReplacementState()
    msgs = tool_msg("x" * 60000)
    original = layer1.spill_single
    calls = []

    def fail(*args):
        calls.append(args)
        raise OSError("磁盘已满")

    monkeypatch.setattr(layer1, "spill_single", fail)
    for _ in range(2):
        assert layer1.offload_and_snip(msgs, state, session) == msgs
    assert len(calls) == 2 and "0" not in state._seen_ids
    monkeypatch.setattr(layer1, "spill_single", original)
    assert layer1.offload_and_snip(msgs, state, session) != msgs


def test_offload_concurrent(tmp_path):
    session = new_session_context(str(tmp_path))
    state = ContentReplacementState()
    msgs = tool_msg("x" * 60000)
    with ThreadPoolExecutor(max_workers=20) as pool:
        outputs = list(
            pool.map(lambda _: layer1.offload_and_snip(msgs, state, session), range(50))
        )
    assert all(out == outputs[0] for out in outputs)
    assert len(list(Path(session.spill_dir).iterdir())) == 1
    assert state.replacement_count() == 1


@pytest.mark.parametrize(
    "id_", ["../escape", "/tmp/out", "a/b", "..", "a\\b", "C:escape"]
)
def test_invalid_spill_name_keeps_original(tmp_path, id_):
    session = new_session_context(str(tmp_path))
    state = ContentReplacementState()
    msgs = [Message("tool", tool_results=[ToolResult(id_, "x" * 60000)])]
    assert layer1.offload_and_snip(msgs, state, session) == msgs
    assert not state.has_seen(id_)
