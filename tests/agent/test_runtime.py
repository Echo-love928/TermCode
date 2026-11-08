"""验证临时提醒与 only_once 的会话边界。"""

from termcode.agent import SessionRuntime
from termcode.compact import new_session_context
from termcode.hook import Engine, Event
from termcode.hook.rule import PromptAction, Rule
from termcode.permission import Mode


async def test_reminders_and_only_once_reset(tmp_path):
    event = Event.SESSION_START
    hook_engine = Engine(
        [Rule("welcome", event, PromptAction("你好"), only_once=True)], []
    )
    runtime = SessionRuntime(
        session=new_session_context(str(tmp_path)),
        hook_engine=hook_engine,
    )
    await runtime.dispatch_hook(event, str(tmp_path), Mode.DEFAULT)
    await runtime.dispatch_hook(event, str(tmp_path), Mode.DEFAULT)
    assert runtime.take_reminders() == ["你好"]
    assert runtime.take_reminders() == []
    runtime.append_reminders(["旧会话"])
    runtime.reset_for_new_session(new_session_context(str(tmp_path)))
    assert runtime.take_reminders() == []
    await runtime.dispatch_hook(event, str(tmp_path), Mode.DEFAULT)
    assert runtime.take_reminders() == ["你好"]
