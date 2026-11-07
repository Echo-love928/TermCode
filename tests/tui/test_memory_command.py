"""记忆命令只列文件名，不读取正文、不修改历史或文件。"""

import pytest
from test_tui import local_config, transcript

from termcode.memory import Manager
from termcode.tui.app import ChatInput, SessionState, TermCodeApp


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
        assert "无已加载的记忆文件" in transcript(app)
        assert app.conv.messages() == []
        assert not sse_server.requests
        assert app.state == SessionState.IDLE
        assert app.query_one("#input", ChatInput).has_focus
    assert not manager.project_store.directory.exists()
    assert not manager.user_store.directory.exists()


async def test_memory_lists_filenames_without_contents(
    sse_server, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    manager = Manager(str(tmp_path / "project"), str(tmp_path / "user"))
    paths = []
    for store in (manager.project_store, manager.user_store):
        store.ensure_dir()
        for name in ("MEMORY.md", "project_knowledge_stack.md"):
            path = store.directory / name
            path.write_text("不可展开的正文 TEST_SECRET", encoding="utf-8")
            paths.append(path)
    before = {path: path.read_bytes() for path in paths}
    app = TermCodeApp([local_config(sse_server)], mem_mgr=manager)
    async with app.run_test(size=(38, 20)) as pilot:
        await pilot.press(*"/memory", "enter")
        text = app._transcript[-1].plain
        assert text.splitlines() == [
            "MEMORY.md",
            "project_knowledge_stack.md",
            "MEMORY.md",
            "project_knowledge_stack.md",
        ]
        assert "不可展开" not in text and "TEST_SECRET" not in text
        assert {path: path.read_bytes() for path in paths} == before
        assert not sse_server.requests and not app.conv.messages()
        assert app.query_one("#composer").region.bottom <= 20
