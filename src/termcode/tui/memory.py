"""将本机保存的两级记忆只读展示在会话区。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from rich.text import Text

if TYPE_CHECKING:
    from termcode.memory import Note
    from termcode.tui.app import TermCodeApp


async def show_memory(app: TermCodeApp) -> None:
    entries: list[tuple[str, str, Note]] = []
    if app.mem_mgr is not None:
        try:
            for level, store in (
                ("user", app.mem_mgr.user_store),
                ("project", app.mem_mgr.project_store),
            ):
                notes = await asyncio.to_thread(store.list_notes)
                entries.extend((level, filename, note) for filename, note in notes)
        except OSError:
            app._write(Text("读取记忆失败，请稍后重试。", style="red"))
            return

    output = Text()
    output.append(f"Memories ({len(entries)} entries):\n", style="bold")
    if not entries:
        output.append(
            "\n暂无已保存的记忆。可以说“记住……”；"
            "后台整理完成后，再输入 /memory 查看。\n",
            style="dim",
        )
    for index, (level, filename, note) in enumerate(entries, 1):
        output.append(f"\n  {index}. [{level}] ", style="cyan")
        output.append(app._clean(filename.removesuffix(".md")), style="dim")
        output.append(" — " + app._clean(note.title) + "\n", style="bold")
        if note.content:
            content = app._clean(note.content).replace("\n", "\n     ")
            output.append("     " + content + "\n", style="none")
    app._write(output)
