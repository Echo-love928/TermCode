"""在隔离目录与用户目录中，用真实模型验证身份补充更新和重启注入。"""

import json
from dataclasses import asdict

import yaml
from tmux_e2e import EVIDENCE, ROOT, TmuxRun

from termcode.config import load


class MemoryUpdateRun(TmuxRun):
    def run(self):
        cfg = next(
            c
            for c in load(ROOT / ".termcode/config.yaml").providers
            if c.protocol == "anthropic"
        )
        entries = [asdict(cfg) | {"thinking": False}]
        base = ROOT / ".cache/tmux-e2e" / self.socket
        isolated_home = base / "home"
        isolated_home.mkdir(parents=True)
        self.tmux("new-session", "-d", "-s", "bootstrap", "sleep 3600")
        self.tmux("set-environment", "-g", "HOME", str(isolated_home))
        instruction = (
            "不要调用工具。本次用户介绍自身时用一句话确认收到；"
            "询问身份时根据已保存的长期记忆回答。"
        )

        def prepare(session):
            work = base / session
            work.mkdir(parents=True)
            (work / "TERMCODE.md").write_text(instruction, encoding="utf-8")
            return work

        def rows(work):
            for archive in (work / ".termcode/sessions").glob("*/conversation.jsonl"):
                try:
                    return [
                        json.loads(line)
                        for line in archive.read_text(encoding="utf-8").splitlines()
                    ]
                except (OSError, ValueError):
                    return []
            return []

        def turn(session, work, text, expected_turns):
            self.send(session, text)
            self.key(session, "Enter")

            def completed():
                messages = rows(work)
                return (
                    len([m for m in messages if m.get("role") == "user"])
                    >= expected_turns
                    and messages[-1].get("role") == "assistant"
                    and messages[-1].get("content")
                    and "Imagining" not in self.capture(session)
                )

            self.wait(completed, "真实模型完成本轮身份对话", seconds=180)
            return rows(work)[-1]["content"]

        session = "memory-identity"
        work = prepare(session)
        self.start(session, entries)
        turn(
            session,
            work,
            "记住我是 Python 工程师，具备深入Python语言专业知识。",
            1,
        )
        user_memory = isolated_home / ".termcode/memory"

        def initial_note():
            for path in user_memory.glob("*.md"):
                if path.name != "MEMORY.md" and "Python" in path.read_text(
                    encoding="utf-8"
                ):
                    return path
            return None

        self.wait(lambda: initial_note() is not None, "首次身份笔记落盘", seconds=180)
        note = initial_note()
        before = note.read_text(encoding="utf-8")
        old_meta = yaml.safe_load(before.split("---", 2)[1])
        (EVIDENCE / "memory-update-first-note.md").write_text(before, encoding="utf-8")
        self.capture(session, "memory-update-first")

        turn(
            session,
            work,
            "我一名28届的计算机毕业生，打算学习最新的AI Agent开发，你能给我啥建议？",
            2,
        )

        def updated():
            text = note.read_text(encoding="utf-8")
            return text != before and "28" in text and "Agent" in text

        self.wait(updated, "第二次自然陈述更新同一身份文件", seconds=180)
        after = note.read_text(encoding="utf-8")
        meta = yaml.safe_load(after.split("---", 2)[1])
        assert meta["created"] == old_meta["created"]
        assert meta["updated"] > old_meta["updated"]
        assert "资深" not in after
        index = (user_memory / "MEMORY.md").read_text(encoding="utf-8")
        assert "28" in index and "Agent" in index
        assert index.count(note.name) == 1
        (EVIDENCE / "memory-update-second-note.md").write_text(after, encoding="utf-8")
        self.capture(session, "memory-update-second")
        print("同一身份笔记、更新时间和索引已更新", flush=True)
        self.finish(session, work)

        session = "memory-identity-restart"
        work = prepare(session)
        self.start(session, entries)
        answer = turn(session, work, "现在告诉我我是谁，我计划学习什么？", 1)
        assert "28" in answer and "Agent" in answer
        assert "资深" not in answer
        self.capture(session, "memory-update-restart")
        self.finish(session, work)
        result = self.results | {
            "backend": "configured live anthropic provider",
            "same_note_updated": True,
            "created_preserved": True,
            "updated_advanced": True,
            "index_updated": True,
            "fresh_process_identity": True,
            "real_user_memory_touched": False,
        }
        (EVIDENCE / "memory-update-live-results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    run = MemoryUpdateRun()
    try:
        run.run()
    finally:
        run.close()
