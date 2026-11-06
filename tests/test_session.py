"""存档恢复、并发与清理的真实文件测试。"""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from termcode.compact import new_session_context
from termcode.conversation import Conversation
from termcode.llm import Message, ToolCall, ToolResult
from termcode.session import Writer, clean_expired, list_sessions, load_session


def test_writer_tool_roundtrip_compact(tmp_path):
    with Writer(str(tmp_path), "model") as writer:
        conv = Conversation(writer.on_append, writer.on_replace)
        conv.add_user("hello")
        conv.add_assistant_with_tool_calls("", [ToolCall("1", "read_file", "{}")])
        conv.add_tool_results([ToolResult("1", "结果")])
        conv.add_assistant("done")
        assert load_session(str(tmp_path)) == conv.messages()
        rows = [
            json.loads(line)
            for line in (tmp_path / "conversation.jsonl").read_text().splitlines()
        ]
        assert rows[0]["model"] == "model"
        assert all("ts" in row for row in rows)
        conv.replace_history([Message("user", "summary")])
        assert load_session(str(tmp_path)) == conv.messages()
        assert '"type":"compact"' in (tmp_path / "conversation.jsonl").read_text()


def test_bad_lines_orphans_and_partial_append(tmp_path):
    with Writer(str(tmp_path)) as writer:
        writer.append(Message("user", "hello"))
    path = tmp_path / "conversation.jsonl"
    with path.open("ab") as stream:
        stream.write(b'{invalid}\n[]\n{"role":"assistant","content":5}\n{partial')
    with Writer.open_existing(str(tmp_path)) as writer:
        writer.append(
            Message("assistant", tool_calls=[ToolCall("x", "read_file", "{}")])
        )
    assert load_session(str(tmp_path)) == [Message("user", "hello")]
    with Writer.open_existing(str(tmp_path)) as writer:
        writer.append(Message("tool", tool_results=[ToolResult("wrong", "bad")]))
    assert load_session(str(tmp_path)) == [Message("user", "hello")]


def test_concurrent_history_and_archive_order(tmp_path):
    with Writer(str(tmp_path)) as writer:
        conv = Conversation(writer.on_append, writer.on_replace)
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(lambda i: conv.add_user(str(i)), range(100)))
        assert load_session(str(tmp_path)) == conv.messages()
        assert conv.length() == 100


async def test_list_cleanup(tmp_path):
    sessions = tmp_path / ".termcode/sessions"
    old = (datetime.now() - timedelta(days=31)).strftime("%Y%m%d-%H%M%S") + "-abcd"
    for name in [old, "1717000000-abc12345"]:
        with Writer(str(sessions / name), "test-model") as writer:
            writer.append(Message("user", "标题" * 50))
    context = new_session_context(str(tmp_path))
    with Writer(context.session_dir) as writer:
        writer.append(Message("user", "最新"))
    infos = list_sessions(str(sessions))
    assert len(infos) == 2
    assert all(len(info.title) <= 50 for info in infos)
    assert infos[0].modified_at >= infos[1].modified_at
    await clean_expired(str(sessions))
    assert not (sessions / old).exists()
    assert (sessions / "1717000000-abc12345").exists()
    assert Path(context.session_dir).exists()  # noqa: ASYNC240


def test_callback_isolation_and_failure():
    calls = []
    conv = Conversation(calls.append)
    conv.add_user("hello")
    calls.clear()
    copied = Conversation.from_messages(conv.messages(), calls.append)
    assert not calls
    copied.add_assistant("world")
    assert conv.length() == 1
    failing = Conversation(lambda msg: 1 / 0)
    failing.add_user("仍可对话")
    assert failing.length() == 1
