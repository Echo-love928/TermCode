"""通过真实输入验证记忆查看、本机刷新和命令隔离。"""

import pytest
from test_tui import local_config, transcript, wait_for

from termcode.memory import Manager
from termcode.tui.app import ChatInput, SessionState, TermCodeApp


def save_note(store, note_type, slug, title, content):
    store.apply(
        [
            dict(
                action="create",
                type=note_type,
                slug=slug,
                title=title,
                content=content,
            )
        ]
    )


@pytest.mark.parametrize("with_manager", [True, False])
async def test_empty_memory_does_not_call_model_or_create_files(
    sse_server, tmp_path, monkeypatch, with_manager
):
    monkeypatch.chdir(tmp_path)
    manager = Manager(str(tmp_path / "project"), str(tmp_path / "user"))
    app = TermCodeApp(
        [local_config(sse_server)], mem_mgr=manager if with_manager else None
    )
    async with app.run_test() as pilot:
        await pilot.press(*"/memory", "enter")
        await wait_for(lambda: "Memories (0 entries):" in transcript(app))
        assert "暂无已保存的记忆" in transcript(app)
        assert app.conv.messages() == []
        assert not sse_server.requests
        assert app.state == SessionState.IDLE
        assert app.query_one("#input", ChatInput).has_focus
    assert not manager.project_store.directory.exists()
    assert not manager.user_store.directory.exists()


async def test_memory_lists_full_notes_refreshes_and_preserves_chat(
    sse_server, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    manager = Manager(str(tmp_path / "project"), str(tmp_path / "user"))
    save_note(
        manager.user_store,
        "user_preference",
        "background",
        "用户画像",
        "28届计算机毕业生。\n学习 AI Agent 开发。\n[red]这是原文[/red] TEST_SECRET",
    )
    save_note(
        manager.project_store,
        "project_knowledge",
        "stack",
        "项目技术栈",
        "使用 Python 和 Textual。",
    )
    paths = [
        path
        for store in (manager.user_store, manager.project_store)
        for path in store.directory.glob("*.md")
    ]
    before = {path: path.read_bytes() for path in paths}
    app = TermCodeApp([local_config(sse_server)], mem_mgr=manager)
    async with app.run_test(size=(38, 20)) as pilot:
        await pilot.press(*"/memory", "enter")
        await wait_for(lambda: "Memories (2 entries):" in transcript(app))
        text = app._transcript[-1].plain
        assert "[user]" in text and "[project]" in text
        assert text.index("[user]") < text.index("[project]")
        assert "用户画像" in text and "28届计算机毕业生" in text
        assert "学习 AI Agent" in text and "Python 和 Textual" in text
        assert "[red]这是原文[/red]" in text
        assert "TEST_SECRET" not in text
        assert {path: path.read_bytes() for path in paths} == before
        assert not sse_server.requests and app.conv.messages() == []
        assert app.query_one("#composer").region.bottom <= 20

        manager.user_store.apply(
            [
                dict(
                    action="update",
                    filename="user_preference_background.md",
                    title="最新用户画像",
                    content="最新目标：学习多智能体开发。",
                )
            ]
        )
        await app.submit("/memory")
        text = app._transcript[-1].plain
        assert "最新用户画像" in text and "多智能体" in text
        assert "28届" not in text
        assert not sse_server.requests and app.conv.messages() == []

        await app.submit("你好")
        await wait_for(lambda: app.state == SessionState.IDLE)
        assert len(sse_server.requests) == 1
        user_messages = [
            msg
            for msg in sse_server.requests[0]["body"]["messages"]
            if msg["role"] == "user"
        ]
        assert [msg["content"] for msg in user_messages] == ["你好"]


async def test_memory_read_failure_is_visible_and_chat_stays_idle(
    sse_server, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    manager = Manager(str(tmp_path / "project"), str(tmp_path / "user"))

    def unreadable():
        raise OSError("本机读取错误")

    monkeypatch.setattr(manager.user_store, "list_notes", unreadable)
    app = TermCodeApp([local_config(sse_server)], mem_mgr=manager)
    async with app.run_test():
        await app.submit("/memory")
        assert "读取记忆失败" in transcript(app)
        assert app.state == SessionState.IDLE
        assert not sse_server.requests and not app.conv.messages()
