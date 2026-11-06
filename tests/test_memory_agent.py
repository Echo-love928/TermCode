"""记忆触发频率、用户信号与后台执行。"""

import asyncio
import json

import pytest
import yaml

from termcode.agent import Agent, SessionRuntime
from termcode.conversation import Conversation
from termcode.llm import StreamEvent
from termcode.memory import Manager
from termcode.tool import Registry


class Provider:
    model = "test-model"
    name = "test"

    async def stream(self, req):
        yield StreamEvent(text="done", done=True)


async def test_trigger_five_and_explicit_snapshot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manager = Manager(str(tmp_path / "project"), str(tmp_path / "user"))
    calls = []
    gate = asyncio.Event()

    async def update(msgs):
        calls.append(msgs)
        await gate.wait()

    manager.update_async = update
    runtime = SessionRuntime()
    agent = Agent(
        Provider(),
        Registry(),
        runtime=runtime,
        memory_manager=manager,
        instruction_text="项目规范",
    )
    conv = Conversation()
    for i in range(6):
        conv.add_user("记住中文" if i == 5 else f"turn {i}")
        async for _ in agent.run(conv):
            pass
    await asyncio.sleep(0)
    assert runtime.turn_count == 6
    assert len(calls) == 2
    assert calls[0][0].content == "turn 0"
    assert [msg.content for msg in calls[0] if msg.role == "user"] == [
        f"turn {i}" for i in range(5)
    ]
    assert calls[1][0].content == "记住中文"
    assert [len(msgs) for msgs in calls] == [10, 2]
    gate.set()
    await manager.close()


@pytest.mark.parametrize(
    "correction",
    [
        "我一名28届的计算机毕业生，打算学习最新的AI Agent开发",
        "Actually, I'm a 2028 computer science graduate learning AI Agent development.",
    ],
)
async def test_identity_correction_updates_existing_note(
    tmp_path, monkeypatch, correction
):
    monkeypatch.chdir(tmp_path)
    filename = "user_preference_background.md"

    class IdentityProvider(Provider):
        def __init__(self):
            self.chat_requests = []
            self.memory_requests = []

        async def stream(self, req):
            if req.system.stable.startswith("你是长期记忆整理器"):
                self.memory_requests.append(req)
                payload = json.loads(req.messages[0].content)
                if correction in [msg["content"] for msg in payload["messages"]]:
                    action = dict(
                        action="update",
                        level="user",
                        filename=filename,
                        title="2028届计算机毕业生，学习AI Agent开发",
                        content="用户是2028届计算机毕业生，正在学习AI Agent开发。",
                    )
                else:
                    action = dict(
                        action="create",
                        level="user",
                        type="user_preference",
                        slug="background",
                        title="资深Python工程师",
                        content="用户是资深Python工程师。",
                    )
                yield StreamEvent(text=json.dumps([action]), done=True)
            else:
                self.chat_requests.append(req)
                yield StreamEvent(text="已确认", done=True)

    provider = IdentityProvider()
    manager = Manager(str(tmp_path / "project"), str(tmp_path / "user"), provider)
    agent = Agent(provider, Registry(), memory_manager=manager)
    conv = Conversation()
    conv.add_user("记住我是资深Python工程师")
    async for _ in agent.run(conv):
        pass
    await manager.close()
    note = manager.user_store.directory / filename
    original = yaml.safe_load(note.read_text(encoding="utf-8").split("---")[1])

    conv.add_user(correction)
    async for _ in agent.run(conv):
        pass
    await manager.close()
    text = note.read_text(encoding="utf-8")
    current = yaml.safe_load(text.split("---")[1])
    assert "2028" in text and "AI Agent" in text
    assert "资深" not in text and "资深" not in manager.load_index()
    assert current["created"] == original["created"]
    assert current["updated"] > original["updated"]
    assert len(list(manager.user_store.directory.glob("user_preference_*.md"))) == 1
    assert len(provider.memory_requests) == 2

    conv.add_user("现在告诉我我是谁")
    async for _ in agent.run(conv):
        pass
    await manager.close()
    assert "2028" in provider.chat_requests[-1].system.stable
    assert "资深" not in provider.chat_requests[-1].system.stable
    assert len(provider.memory_requests) == 2
