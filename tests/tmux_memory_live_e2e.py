"""用项目已配置的真实模型验收指令、工具、记忆和跨进程恢复。"""

import json
import shlex
import sys
from dataclasses import asdict

from tmux_e2e import EVIDENCE, ROOT, TmuxRun

from termcode.config import load


class LiveRun(TmuxRun):
    def run(self):
        cfg = next(
            c
            for c in load(ROOT / ".termcode/config.yaml").providers
            if c.protocol == "anthropic"
        )
        session = "ch09-live"
        work = ROOT / ".cache/tmux-e2e" / self.socket / session
        (work / "home").mkdir(parents=True)
        self.tmux("new-session", "-d", "-s", "bootstrap", "sleep 3600")
        self.tmux("set-environment", "-g", "HOME", str(work / "home"))
        (work / "TERMCODE.md").write_text("@include rules.md", encoding="utf-8")
        (work / "rules.md").write_text("所有回复以「验收：」开头。", encoding="utf-8")
        (work / "sample.txt").write_text("CH09_SAMPLE_7391", encoding="utf-8")
        self.start(session, [asdict(cfg) | {"thinking": False}])
        self.send(
            session,
            "请仅用 read_file 读取 sample.txt。记住：此项目代号是蓝鲸7391。"
            "这是长期项目知识。回复文件内容与项目代号。",
        )
        self.key(session, "Enter")
        sessions = work / ".termcode/sessions"

        def completed():
            for path in sessions.glob("*/conversation.jsonl"):
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                if (
                    rows
                    and rows[-1].get("role") == "assistant"
                    and rows[-1].get("content")
                ):
                    return rows
            return None

        self.wait(lambda: bool(completed()), "真实模型工具调用并回复", seconds=180)
        rows = completed()
        assert any(row.get("tool_calls") for row in rows)
        assert any(row.get("tool_results") for row in rows)
        assert "验收：" in rows[-1]["content"]
        assert "CH09_SAMPLE_7391" in rows[-1]["content"]
        memory = work / ".termcode/memory/MEMORY.md"
        self.wait(memory.exists, "真实模型生成项目笔记", seconds=180)
        assert "7391" in memory.read_text()
        self.capture(session, "ch09-live-tool-memory")
        archive = next(sessions.glob("*/conversation.jsonl"))
        before = len(archive.read_text().splitlines())
        self.finish(session, work)
        command = shlex.join([sys.executable, "-m", "termcode"])
        self.tmux(
            "new-session",
            "-d",
            "-s",
            "ch09-live-restart",
            "-x",
            "110",
            "-y",
            "40",
            "-c",
            str(work),
            command,
        )
        session = "ch09-live-restart"
        self.tmux("set-option", "-t", session, "remain-on-exit", "on")
        self.wait(
            lambda: "Send a message" in self.capture(session), "新进程启动", seconds=120
        )
        self.send(session, "/resume")
        self.key(session, "Enter")
        self.wait(lambda: "恢复会话" in self.capture(session), "真实历史列表")
        self.send(session, "sample.txt")
        self.wait(lambda: "1 项" in self.capture(session), "搜索旧会话")
        self.key(session, "Enter")
        self.wait(lambda: "已恢复会话" in self.capture(session), "恢复旧会话")
        self.send(session, "不要调用工具，告诉我项目代号和刚才文件内容。")
        self.key(session, "Enter")

        def resumed():
            current = [json.loads(line) for line in archive.read_text().splitlines()]
            return (
                len(current) >= before + 2
                and current[-1].get("role") == "assistant"
                and "7391" in current[-1].get("content", "")
            )

        self.wait(resumed, "恢复后真实模型引用上下文", seconds=180)
        self.capture(session, "ch09-live-resumed")
        self.send(session, "/exit")
        self.key(session, "Enter")
        result = {
            "backend": "configured live anthropic provider",
            "tool": True,
            "instructions": True,
            "memory_created": True,
            "restart_resume": True,
            "same_archive_append": True,
        }
        (EVIDENCE / "ch09-live-results.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    run = LiveRun()
    try:
        run.run()
    finally:
        run.close()
