"""串行整理记忆，后台更新不阻塞主对话。"""

import asyncio
import json
import logging
from dataclasses import asdict

from termcode.llm import Message, Provider, Request, System

from .prompts import MEMORY_UPDATE_SYSTEM_PROMPT
from .store import Store


class Manager:
    def __init__(
        self,
        project_dir: str,
        user_dir: str,
        provider: Provider | None = None,
        model: str = "",
    ) -> None:
        self.project_store = Store(project_dir)
        self.user_store = Store(user_dir)
        self.provider = provider
        self.model = model
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    def set_provider(self, provider: Provider, model: str) -> None:
        self.provider, self.model = provider, model

    def list_files(self) -> tuple[list[str], list[str]]:
        """只列文件名，不读取笔记正文或创建目录。"""
        results = []
        for store in (self.project_store, self.user_store):
            try:
                names = sorted(
                    path.name
                    for path in store.directory.iterdir()
                    if path.suffix == ".md" and path.is_file() and not path.is_symlink()
                )
            except FileNotFoundError:
                names = []
            except OSError:
                logging.getLogger(__name__).warning("无法列出记忆文件")
                names = []
            results.append(names)
        return results[0], results[1]

    def cancel_pending(self) -> None:
        """退出时立即通知所有后台记忆任务取消。"""
        for task in self._tasks:
            task.cancel()

    def load_index(self) -> str:
        parts = []
        for label, store in [
            ("项目级", self.project_store),
            ("用户级", self.user_store),
        ]:
            text = store.load_index()
            if text.strip():
                parts.append(f"{label}记忆（详情目录：{store.directory}）\n{text}")
        data = "\n\n".join(parts).encode()
        marker = b"\n(index truncated)"
        if len(data) > 25 * 1024:
            return (
                data[: 25 * 1024 - len(marker)].decode(errors="ignore")
                + marker.decode()
            )
        return data.decode()

    def schedule(self, recent_msgs: list[Message]) -> None:
        task = asyncio.create_task(self.update_async(recent_msgs))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        if self._tasks:
            _, pending = await asyncio.wait(self._tasks, timeout=2)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def update_async(self, recent_msgs: list[Message]) -> None:
        async with self._lock:
            if self.provider is None:
                return
            try:
                payload = {
                    "index": self.load_index(),
                    "messages": [asdict(msg) for msg in recent_msgs],
                }
                req = Request(
                    [Message("user", json.dumps(payload, ensure_ascii=False))],
                    tools=[],
                    system=System(MEMORY_UPDATE_SYSTEM_PROMPT),
                )
                text = ""
                completed = False
                async for event in self.provider.stream(req):
                    if event.err:
                        raise event.err
                    if event.tool_calls:
                        raise ValueError("记忆更新不得调用工具")
                    text += event.text
                    if event.done:
                        completed = True
                        break
                if not completed:
                    raise ValueError("记忆响应未完成")
                actions = json.loads(text)
                if not isinstance(actions, list) or not all(
                    isinstance(a, dict) for a in actions
                ):
                    raise ValueError("记忆响应必须为操作数组")
                if any(a.get("level") not in {"project", "user"} for a in actions):
                    raise ValueError("非法记忆层级")
                for level, store in [
                    ("project", self.project_store),
                    ("user", self.user_store),
                ]:
                    selected = [a for a in actions if a["level"] == level]
                    if selected:
                        await asyncio.to_thread(store.apply, selected)
            except Exception:
                logging.getLogger(__name__).exception("记忆更新失败，已跳过")
