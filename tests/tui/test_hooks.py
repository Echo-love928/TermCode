"""通过 Textual 输入和会话命令验证 Hook 的界面接入。"""

from test_tui import local_config, transcript
from textual.widgets import Static

from termcode.hook import Engine, Event
from termcode.hook.executor import ExecutionResult
from termcode.hook.rule import PromptAction, Rule
from termcode.llm import Message
from termcode.session import Writer, list_sessions
from termcode.tui.app import ChatInput, TermCodeApp
from termcode.tui.resume import do_resume_session


async def test_submit_blocked_keeps_draft_and_focus(sse_server, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    event = Event.USER_PROMPT_SUBMIT
    engine = Engine([Rule("block", event, PromptAction(""))], [])

    async def execute(rule, payload, *, blocking):
        assert blocking and payload["prompt"] == "保留这条草稿"
        return ExecutionResult(blocked=True, reason="blocked by policy")

    monkeypatch.setattr(engine._executor, "run", execute)
    app = TermCodeApp([local_config(sse_server)], hook_engine=engine)
    async with app.run_test() as pilot:
        editor = app.query_one(ChatInput)
        editor.load_text("保留这条草稿")
        await pilot.pause()
        await pilot.press("enter")
        error = app.query_one("#submit-error", Static)
        assert error.display and "[hook block] blocked by policy" in str(error.render())
        assert editor.text == "保留这条草稿" and editor.has_focus
        assert app.conv.length() == 0 and not sse_server.requests
        await pilot.press("backspace")
        assert not error.display


async def test_mount_clear_resume_and_exit_dispatch_once(
    sse_server, monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    events = [Event.SESSION_START, Event.SESSION_END, Event.SESSION_RESUME]
    engine = Engine(
        [
            Rule(event.value, event, PromptAction(""), only_once=True)
            for event in events
        ],
        ["project/hooks.yaml", "user/hooks.yaml"],
    )
    seen = []

    async def execute(rule, payload, *, blocking):
        seen.append((rule.event, payload["session_id"]))
        return ExecutionResult(prompt=rule.event.value)

    monkeypatch.setattr(engine._executor, "run", execute)
    app = TermCodeApp([local_config(sse_server)], hook_engine=engine)
    async with app.run_test():
        start = app.runtime.session
        with Writer(start.session_dir) as writer:
            writer.append(Message("user", "旧对话"))
            writer.append(Message("assistant", "旧回复"))
        assert app.runtime.take_reminders() == ["SessionStart"]
        await app.submit("/hooks")
        assert "SessionStart  SessionStart  prompt  [once]" in transcript(app)
        assert "Loaded from: project/hooks.yaml, user/hooks.yaml" in transcript(app)
        await app.submit("/clear")
        new_id = app.runtime.session.session_id
        assert app.runtime.take_reminders() == ["SessionStart"]
        previous = next(
            info
            for info in list_sessions(app.sessions_dir)
            if info.id == start.session_id
        )
        await do_resume_session(app, previous)
        assert app.runtime.take_reminders() == ["SessionResume"]
        await app.submit("/exit")
    assert seen == [
        (Event.SESSION_START, start.session_id),
        (Event.SESSION_END, start.session_id),
        (Event.SESSION_START, new_id),
        (Event.SESSION_END, new_id),
        (Event.SESSION_RESUME, start.session_id),
        (Event.SESSION_END, start.session_id),
    ]
