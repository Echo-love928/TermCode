"""ch09：真实 tmux、CLI 和本地双协议 SSE 服务的端到端验收。"""

import json
import shlex
import sys

from tmux_e2e import EVIDENCE, TmuxRun


class MemoryRun(TmuxRun):
    def run(self):
        for protocol in ("openai", "anthropic"):
            session = f"ch09-{protocol}"
            work = self.start(session, [self.entry(protocol, protocol)])
            self.tmux("set-environment", "-g", "HOME", str(work / "home"))
            # 冷启动后再次启动以加载独立工作区的指令，避免碰到用户数据。
            self.finish(session, work)
            (work / "home").mkdir(exist_ok=True)
            (work / "TERMCODE.md").write_text("@include rules.md", encoding="utf-8")
            (work / "rules.md").write_text(
                "CH09_PROJECT_RULE：使用中文。", encoding="utf-8"
            )
            command = shlex.join([sys.executable, "-m", "termcode"])
            self.tmux(
                "new-session",
                "-d",
                "-s",
                session + "-again",
                "-x",
                "110",
                "-y",
                "40",
                "-c",
                str(work),
                command,
            )
            session += "-again"
            self.tmux("set-option", "-t", session, "remain-on-exit", "on")
            self.wait(
                lambda session=session: "Send a message" in self.capture(session),
                "重启",
                seconds=120,
            )
            self.server.responses = [
                {
                    "calls": [
                        {
                            "id": "read-1",
                            "name": "read_file",
                            "input": json.dumps({"path": str(work / "rules.md")}),
                        }
                    ]
                },
                {"text": "CH09_TOOL_DONE"},
                {
                    "text": json.dumps(
                        [
                            {
                                "action": "create",
                                "level": "project",
                                "type": "project_knowledge",
                                "slug": "language",
                                "title": "项目中文",
                                "content": "此项目使用中文",
                            }
                        ]
                    )
                },
            ]
            before = len(self.server.requests)
            self.send(session, "读取 rules.md，记住这个项目使用中文")
            self.key(session, "Enter")
            self.wait(
                lambda session=session: "CH09_TOOL_DONE" in self.capture(session),
                "工具调用及回复",
            )
            memory = work / ".termcode/memory/MEMORY.md"
            self.wait(memory.exists, "后台记忆更新")
            requests = self.server.requests[before:]
            assert len(requests) == 3
            assert "CH09_PROJECT_RULE" in json.dumps(
                requests[0]["body"], ensure_ascii=False
            )
            assert not requests[-1]["body"].get("tools")
            files = list((work / ".termcode/sessions").glob("*/conversation.jsonl"))
            archive = next(p for p in files if "CH09_TOOL_DONE" in p.read_text())
            rows = [json.loads(line) for line in archive.read_text().splitlines()]
            assert rows[0]["model"] == "test-model"
            assert [r["role"] for r in rows] == [
                "user",
                "assistant",
                "tool",
                "assistant",
            ]
            self.capture(session, f"{protocol}-ch09-tool-memory")
            self.server.responses = [{"text": "CH09_MEMORY_INJECTED"}]
            self.send(session, "继续对话")
            self.key(session, "Enter")
            self.wait(
                lambda session=session: "CH09_MEMORY_INJECTED" in self.capture(session),
                "记忆注入后的对话",
            )
            assert "项目中文" in json.dumps(
                self.server.requests[-1]["body"], ensure_ascii=False
            )
            self.wait(
                lambda archive=archive: len(archive.read_text().splitlines()) == 6,
                "第二轮存档完成",
            )
            count = len(archive.read_text().splitlines())
            self.send(session, "/resume")
            self.key(session, "Enter")
            self.wait(
                lambda session=session: "恢复会话" in self.capture(session), "恢复列表"
            )
            self.capture(session, f"{protocol}-ch09-resume-list")
            self.send(session, "读取 rules.md")
            self.wait(
                lambda session=session: "1 项" in self.capture(session), "搜索过滤"
            )
            self.key(session, "Escape")
            self.wait(
                lambda session=session: "恢复会话 ·" not in self.capture(session),
                "取消恢复",
            )
            self.send(session, "/resume")
            self.key(session, "Enter")
            self.wait(
                lambda session=session: "恢复会话 ·" in self.capture(session),
                "重新打开列表",
            )
            self.send(session, "读取 rules.md")
            self.wait(
                lambda session=session: "1 项" in self.capture(session), "定位存档"
            )
            self.key(session, "Enter")
            self.wait(
                lambda session=session: "已恢复会话" in self.capture(session),
                "恢复成功",
            )
            self.server.responses = [{"text": "CH09_RESUMED_DONE"}]
            self.send(session, "上次说到哪里，继续")
            self.key(session, "Enter")
            self.wait(
                lambda session=session: "CH09_RESUMED_DONE" in self.capture(session),
                "恢复后对话",
            )
            self.wait(
                lambda archive=archive, count=count: (
                    len(archive.read_text().splitlines()) == count + 2
                ),
                "恢复后回复刷盘",
            )
            self.capture(session, f"{protocol}-ch09-resumed")
            self.send(session, "/exit")
            self.key(session, "Enter")
            self.wait(
                lambda session=session: "CH09_RESUMED_DONE" in self.capture(session),
                "退出保留存档",
            )
            self.results[protocol] = {
                "archive": str(archive),
                "rows": count + 2,
                "tools": True,
                "memory": True,
                "resume": True,
            }
        (EVIDENCE / "ch09-tmux-results.json").write_text(
            json.dumps(self.results, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    run = MemoryRun()
    try:
        run.run()
        print(json.dumps(run.results, ensure_ascii=False, indent=2))
    finally:
        run.close()
