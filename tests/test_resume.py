"""使用真实 Textual 事件验证会话选择、恢复与继续存档。"""

import json
import time
from pathlib import Path

from test_tui import local_config, wait_for
from textual.widgets import Input, OptionList

from termcode.compact import new_session_context
from termcode.llm import Message
from termcode.session import Writer, load_session
from termcode.tui.app import SessionState, TermCodeApp
from termcode.tui.resume import ResumeScreen


async def test_resume_search_cancel_select_append(sse_server, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    contexts = [new_session_context(str(tmp_path)) for _ in range(3)]
    for i, context in enumerate(contexts):
        with Writer(context.session_dir, "old-model") as writer:
            writer.append(Message("user", f"history-{i}"))
            writer.append(Message("assistant", "old reply"))
    current = new_session_context(str(tmp_path))
    writer = Writer(current.session_dir)
    app = TermCodeApp([local_config(sse_server)], writer=writer)
    async with app.run_test(size=(100, 36)) as pilot:
        await app.submit("/resume")
        await pilot.pause()
        assert app.state == SessionState.RESUMING
        screen = app.screen
        assert isinstance(screen, ResumeScreen)
        assert screen.query_one(OptionList).option_count == 4
        await pilot.press("down")
        assert screen.query_one(OptionList).highlighted == 1
        screen.query_one(Input).value = "history-1"
        await pilot.pause()
        assert screen.query_one(OptionList).option_count == 1
        await pilot.press("escape")
        assert app.state == SessionState.IDLE
        assert app.writer is writer
        await app.submit("/resume")
        await pilot.pause()
        app.screen.query_one(Input).value = "history-1"
        await pilot.pause()
        await pilot.press("enter")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert app.runtime.session.session_id == contexts[1].session_id
        assert app.runtime.usage_anchor == 0
        await app.submit("continue")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert len(load_session(contexts[1].session_dir)) == 4


async def test_resume_stale_and_invalid(sse_server, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    context = new_session_context(str(tmp_path))
    with Writer(context.session_dir) as writer:
        writer.append(Message("user", "old"))
        writer.append(Message("assistant", "reply"))
    path = Path(context.session_dir) / "conversation.jsonl"
    rows = [
        json.loads(line) | {"ts": int(time.time() - 7 * 3600)}
        for line in path.read_text().splitlines()
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n{bad}\n")
    app = TermCodeApp([local_config(sse_server)])
    async with app.run_test(size=(100, 36)) as pilot:
        await app.submit("/resume")
        await pilot.pause()
        await pilot.press("enter")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert "暂停" in app.conv.messages()[-1].content
        app.state = SessionState.STREAMING
        await app.submit("/resume")
        assert app.state == SessionState.STREAMING
        app.state = SessionState.IDLE


async def test_resume_over_budget_compacts_before_switch(
    sse_server, tmp_path, monkeypatch
):
    from termcode.session import list_sessions
    from termcode.tui.resume import do_resume_session

    monkeypatch.chdir(tmp_path)
    context = new_session_context(str(tmp_path))
    with Writer(context.session_dir) as writer:
        writer.append(Message("user", "大段内容" * 1000))
        writer.append(Message("assistant", "旧回答"))
    app = TermCodeApp([local_config(sse_server)])
    calls = []

    async def compact(agent, conv, defs):
        calls.append(conv.length())
        conv.replace_history([Message("user", "摘要")])
        return 10000, 10

    monkeypatch.setattr("termcode.agent.Agent.run_force_compact", compact)
    async with app.run_test():
        app.runtime.context_window = 34000
        await do_resume_session(app, list_sessions(app.sessions_dir)[0])
        assert calls == [2]
        assert app.conv.messages() == [Message("user", "摘要")]
        archive = Path(context.session_dir) / "conversation.jsonl"
        assert '"type":"compact"' in archive.read_text()


async def test_resume_failed_compaction_preserves_current(
    sse_server, tmp_path, monkeypatch
):
    from termcode.session import list_sessions
    from termcode.tui.resume import do_resume_session

    monkeypatch.chdir(tmp_path)
    context = new_session_context(str(tmp_path))
    with Writer(context.session_dir) as writer:
        writer.append(Message("user", "内容" * 10000))
    app = TermCodeApp([local_config(sse_server)])

    async def fail(*args):
        raise RuntimeError("测试摘要失败")

    monkeypatch.setattr("termcode.agent.Agent.run_force_compact", fail)
    async with app.run_test():
        previous = app.runtime
        app.runtime.context_window = 34000
        await do_resume_session(app, list_sessions(app.sessions_dir)[0])
        assert app.runtime is previous
        assert app.state == SessionState.IDLE
