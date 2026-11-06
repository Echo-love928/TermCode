"""记忆 CRUD、边界、并发和模型请求协议。"""

import asyncio
import json

import pytest
import yaml

from termcode.llm import Message, StreamEvent
from termcode.memory import Manager, Store


def create(slug="language", level="project"):
    return dict(
        action="create",
        level=level,
        type="project_knowledge",
        slug=slug,
        title="项目中文",
        content="代码注释使用中文",
    )


def test_store_crud_and_path_boundary(tmp_path):
    store = Store(str(tmp_path))
    store.apply([create()])
    path = tmp_path / "project_knowledge_language.md"
    before = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])
    store.apply(
        [dict(action="update", filename=path.name, title="新标题", content="新内容")]
    )
    after = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])
    assert after["created"] == before["created"]
    assert after["updated"] >= before["updated"]
    assert "新标题" in store.load_index() and path.name in store.load_index()
    with pytest.raises(ValueError):
        store.apply([dict(action="delete", filename="../outside.md")])
    store.apply([dict(action="delete", filename=path.name)])
    assert not path.exists() and store.load_index() == ""


def test_index_order_truncate(tmp_path):
    manager = Manager(str(tmp_path / "project"), str(tmp_path / "user"))
    for store in [manager.project_store, manager.user_store]:
        store.ensure_dir()
        (store.directory / "MEMORY.md").write_text(
            str(store.directory), encoding="utf-8"
        )
    assert manager.load_index().index("项目级") < manager.load_index().index("用户级")
    (manager.project_store.directory / "MEMORY.md").write_text(
        "中" * 20000, encoding="utf-8"
    )
    index = manager.load_index()
    assert len(index.encode()) <= 25 * 1024
    assert index.endswith("(index truncated)")


class MemoryProvider:
    model = "test-model"
    name = "test"

    def __init__(self):
        self.requests = []

    async def stream(self, req):
        self.requests.append(req)
        await asyncio.sleep(0.01)
        yield StreamEvent(text=json.dumps([create(str(len(self.requests)))]))
        yield StreamEvent(done=True)

    async def aclose(self):
        pass


async def test_update_concurrent_and_failure(tmp_path, caplog):
    provider = MemoryProvider()
    manager = Manager(str(tmp_path / "project"), str(tmp_path / "user"), provider)
    await asyncio.gather(
        *(manager.update_async([Message("user", "记住中文")]) for _ in range(8))
    )
    assert len(manager.project_store.load_index().splitlines()) == 8
    assert all(not req.tools for req in provider.requests)
    assert "project_knowledge_1.md" in provider.requests[1].messages[0].content

    async def broken(req):
        yield StreamEvent(text="bad", done=True)

    provider.stream = broken
    await manager.update_async([])
    assert "记忆更新失败" in caplog.text


def test_overflow_and_invalid_batch_roll_back(tmp_path):
    store = Store(str(tmp_path))
    with pytest.raises(ValueError):
        store.apply([create("one"), dict(action="delete", filename="../outside.md")])
    assert not list(tmp_path.glob("*.md"))
    with pytest.raises(ValueError):
        store.apply([create(f"note_{i}") for i in range(201)])
    assert not list(tmp_path.glob("*.md"))


def test_list_notes_reads_full_body_and_skips_invalid_notes(tmp_path):
    store = Store(str(tmp_path))
    content = "完整正文" * 100
    store.apply([create() | {"content": content}])
    index = (tmp_path / "MEMORY.md").read_bytes()
    (tmp_path / "project_knowledge_broken.md").write_text(
        "无 frontmatter", encoding="utf-8"
    )
    (tmp_path / "user_preference_invalid.md").write_text(
        "---\ntitle: [错误标题]\ntype: user_preference\n---\n内容", encoding="utf-8"
    )
    (tmp_path / "unrelated.md").write_text("不应读取的文件", encoding="utf-8")
    notes = store.list_notes()
    assert len(notes) == 1
    assert notes[0][0] == "project_knowledge_language.md"
    assert notes[0][1].content == content
    assert (tmp_path / "MEMORY.md").read_bytes() == index
