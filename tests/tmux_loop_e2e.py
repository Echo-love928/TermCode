"""ch04 tmux 验收：本地 SSE 确定性边界测试，以及 --live 真实模型。"""

import json
import sys
import time
from dataclasses import asdict

from test_tool_protocols import call
from tmux_e2e import EVIDENCE, ROOT, TmuxRun

from termcode.config import load


class LoopRun(TmuxRun):
    def __init__(self, live=False, cancel_only=False):
        super().__init__()
        self.live = live
        self.cancel_only = cancel_only
        self.secrets = []
        self.results["backend"] = (
            "configured live models" if live else "local SSE fixture"
        )
        self.server.delay = 0.08

    def capture(self, session, name=None):
        text = super().capture(session, name)
        assert all(secret not in text for secret in self.secrets if secret)
        return text

    def idle(self, session):
        pane = self.capture(session)
        return (
            "Send a message..." in pane
            and "Imagining" not in pane
            and "Running…" not in pane
        )

    def ask(self, session, prompt, marker, responses=None):
        if responses is not None:
            self.server.responses = responses
        self.send(session, prompt)
        self.key(session, "Enter")
        self.wait(
            lambda: marker in self.capture(session) and self.idle(session),
            marker,
            seconds=180,
        )
        time.sleep(0.15)

    def process_metrics(self, session):
        """只查看本次 tmux 内的 Python 进程，检查取消后的资源与子进程。"""
        from pathlib import Path

        def children(pid):
            path = Path(f"/proc/{pid}/task/{pid}/children")
            return (
                [int(value) for value in path.read_text().split()]
                if path.exists()
                else []
            )

        pending = [
            int(self.tmux("display-message", "-p", "-t", session, "#{pane_pid}"))
        ]
        python_pid = None
        while pending:
            pid = pending.pop()
            command = Path(f"/proc/{pid}/cmdline").read_bytes()
            if b"-m\x00termcode" in command:
                python_pid = pid
                break
            pending.extend(children(pid))
        assert python_pid is not None
        status = Path(f"/proc/{python_pid}/status").read_text()
        fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
        descendants = children(python_pid)
        assert not descendants, "取消后仍有工具子进程"
        return {
            "rss_kib": int(fields["VmRSS"].split()[0]),
            "threads": int(fields["Threads"]),
            "file_descriptors": len(list(Path(f"/proc/{python_pid}/fd").iterdir())),
            "child_processes": len(descendants),
        }

    def check_fixture(self, protocol, session, work):
        source = work / "source.txt"
        source.write_text(
            "Agent Loop 让模型持续使用工具，直到完成任务。\n" * 2100, encoding="utf-8"
        )
        target = work / "summary.txt"
        previous = len(self.server.requests)
        self.server.responses = [
            {
                "text": "先并发读取依据。",
                "calls": [
                    call("read_file", path=str(source)),
                    call("read_file", id="c2", path=str(source)),
                ],
            },
            {
                "text": "根据读取内容写摘要。",
                "calls": [
                    call(
                        "write_file", path=str(target), content="多轮工具循环完成任务。"
                    )
                ],
            },
            {"text": "多轮摘要完成。"},
        ]
        self.send(session, f"读 {source}，根据内容新建 {target}，写一句话摘要。")
        self.key(session, "Enter")
        self.wait(lambda: "第 2 轮" in self.capture(session), "第二轮进度")
        second = self.capture(session, f"ch04-{protocol}-iteration2")
        self.wait(lambda: "第 3 轮" in self.capture(session), "第三轮进度")
        third = self.capture(session, f"ch04-{protocol}-iteration3")
        assert "↑" in second and "tok" in third
        self.wait(
            lambda: "多轮摘要完成" in self.capture(session) and self.idle(session),
            "多轮最终回复",
        )
        assert target.read_text(encoding="utf-8") == "多轮工具循环完成任务。"
        assert len(self.server.requests) - previous == 3
        self.capture(session, f"ch04-{protocol}-multiround")

        resource_samples = [self.process_metrics(session)]
        # 慢 shell 工具期间继续计时和刷新，两个取消按键均留在同一会话。
        for index, key in enumerate(("Escape", "C-c", "Escape")):
            self.server.responses = [
                {"calls": [call("bash", command="sleep 20; echo should-not-finish")]}
            ]
            self.send(session, "运行一个慢命令，然后继续后续步骤。")
            self.key(session, "Enter")
            self.wait(lambda: "Running…" in self.capture(session), "慢命令运行中")
            time.sleep(1.1)
            self.capture(session, f"ch04-{protocol}-running-{index}")
            self.key(session, key)
            self.wait(lambda: self.idle(session), "取消回到空闲")
            self.capture(session, f"ch04-{protocol}-cancel-{index}")
            self.ask(
                session,
                "继续对话，确认刚才已取消。",
                f"取消后继续{index}",
                [{"text": f"取消后继续{index}完成。"}],
            )

            resource_samples.append(self.process_metrics(session))
        warmed = resource_samples[1:]
        assert (
            max(s["file_descriptors"] for s in warmed)
            - min(s["file_descriptors"] for s in warmed)
            <= 2
        )
        assert (
            max(s["rss_kib"] for s in warmed) - min(s["rss_kib"] for s in warmed) < 8192
        )
        assert (
            max(s["threads"] for s in warmed) - min(s["threads"] for s in warmed) <= 1
        )

        self.server.mode = "connection_error"
        self.send(session, "测试网络错误。")
        self.key(session, "Enter")
        self.wait(
            lambda: "网络连接失败" in self.capture(session) and self.idle(session),
            "连接失败且仍可输入",
        )
        self.capture(session, f"ch04-{protocol}-error")
        self.server.mode = "normal"
        self.ask(
            session, "恢复正常对话。", "网络恢复完成", [{"text": "网络恢复完成。"}]
        )
        self.capture(session, f"ch04-{protocol}-recovered")

        self.send(session, "/plan")
        self.key(session, "Enter")
        self.wait(lambda: "PLAN" in self.capture(session), "进入计划模式")
        target = work / "planned.txt"
        previous = len(self.server.requests)
        self.ask(
            session,
            f"先读 {source}，计划创建 {target}。",
            "只读计划完成",
            [
                {"calls": [call("read_file", path=str(source))]},
                {"text": "只读计划完成：下一步创建摘要文件，等待 /do。"},
            ],
        )
        assert not target.exists()
        for request in self.server.requests[previous:]:
            definitions = request["body"]["tools"]
            names = [
                d["name"] if protocol == "anthropic" else d["function"]["name"]
                for d in definitions
            ]
            assert names == ["read_file", "glob", "grep"]
        self.capture(session, f"ch04-{protocol}-plan")
        self.ask(
            session,
            "/do",
            "执行计划完成",
            [
                {"calls": [call("write_file", path=str(target), content="计划已执行")]},
                {"text": "执行计划完成。"},
            ],
        )
        assert target.read_text(encoding="utf-8") == "计划已执行"
        self.capture(session, f"ch04-{protocol}-do")
        self.ask(
            session,
            "你能做什么？",
            "能力说明完成",
            [
                {
                    "text": (
                        "我可以跨多步调用工具，读取、修改文件并执行命令直到完成任务。"
                        "能力说明完成。"
                    )
                }
            ],
        )
        self.finish(session, work, ctrl_c=True)
        final = self.capture(session, f"ch04-{protocol}-exit")
        assert final.index("先并发读取依据") < final.index("● read_file(")
        assert final.index("● read_file(") < final.index("根据读取内容写摘要")
        assert final.index("● write_file(") < final.index("多轮摘要完成")
        assert "[truncated]" in final
        assert "TEST_SECRET" not in final
        self.results[protocol] = {
            "multi_round": True,
            "cancel_escape_ctrl_c_resume": True,
            "connection_error_recovery": True,
            "plan_do": True,
            "ordered_transcript": True,
            "truncated_output": True,
            "usage_and_iterations": True,
            "exit_restored_terminal": True,
            "cancel_resource_samples": resource_samples,
        }

    def check_live_cancel(self, protocol, session, work):
        """使用外部模型调起慢工具，验证两种取消键及同会话继续。"""
        for index, key in enumerate(("Escape", "C-c")):
            self.send(
                session,
                "请实际调用 bash 执行 sleep 20; echo cancel-probe，"
                "收到工具结果后再回复。",
            )
            self.key(session, "Enter")
            self.wait(
                lambda: "Running…" in self.capture(session),
                "真实模型慢工具开始",
                seconds=120,
            )
            self.capture(session, f"ch04-live-{protocol}-running-{index}")
            self.key(session, key)
            self.wait(lambda: self.idle(session), "真实模型取消后回空闲")
            assert "已取消" in self.capture(session)
            assert "TERMCODE_EXIT=" not in self.capture(session)
            self.capture(session, f"ch04-live-{protocol}-cancel-{index}")
            self.ask(
                session,
                f"刚才的工具已由用户取消，请停止原任务，不再调工具，"
                f"只回复‘真实取消后继续{index}完成’。",
                f"真实取消后继续{index}完成",
            )
            self.capture(session, f"ch04-live-{protocol}-resume-{index}")
            assert self.process_metrics(session)["child_processes"] == 0
        return {"escape_cancel_resume": True, "ctrl_c_cancel_resume": True}

    def check_live(self, protocol, session, work):
        source = work / "source.txt"
        source.write_text(
            "Agent Loop 持续跨多轮调用工具，根据结果调整操作，直到任务完成。\n",
            encoding="utf-8",
        )
        target = work / "summary.txt"
        self.ask(
            session,
            f"请先实际调用 read_file 读取 {source}，拿到结果后再用 write_file "
            f"创建 {target}，写一句中文摘要。不要用 bash。"
            "必须分两轮调用工具。完成后回复‘真实多轮完成’。",
            "真实多轮完成",
        )
        assert target.exists() and target.read_text(encoding="utf-8").strip()
        self.capture(session, f"ch04-live-{protocol}-multiround")
        planned = work / "planned.txt"
        self.send(session, "/plan")
        self.key(session, "Enter")
        self.wait(lambda: "PLAN" in self.capture(session), "真实计划模式")
        self.ask(
            session,
            f"先实际用 read_file 读取 {source}，然后提出一个计划：创建 {planned} "
            "并写一句话摘要。此阶段只调查和规划，不写文件。计划末尾写‘真实计划完成’。"
            "执行完成时的最终答复末尾写‘真实执行完成’。",
            "真实计划完成",
        )
        assert not planned.exists()
        self.capture(session, f"ch04-live-{protocol}-plan")
        self.ask(session, "/do", "真实执行完成")
        assert planned.exists() and planned.read_text(encoding="utf-8").strip()
        self.capture(session, f"ch04-live-{protocol}-do")
        self.ask(
            session, "你能做什么？请简短说明，最后写‘真实能力完成’。", "真实能力完成"
        )
        self.capture(session, f"ch04-live-{protocol}-capabilities")
        cancelled = self.check_live_cancel(protocol, session, work)
        self.finish(session, work)
        final = self.capture(session, f"ch04-live-{protocol}-exit")
        assert "● read_file(" in final and "● write_file(" in final
        self.results[protocol] = {
            **cancelled,
            "multi_round_read_write": True,
            "plan_do": True,
            "capabilities": True,
            "terminal_restored": True,
        }

    def run(self):
        entries = []
        if self.live:
            config = load(ROOT / ".termcode/config.yaml")
            entries = [asdict(p) for p in config.providers]
            self.secrets = [p.api_key for p in config.providers]
        for protocol in ("openai", "anthropic"):
            entry = (
                next((p for p in entries if p["protocol"] == protocol), None)
                if self.live
                else self.entry(protocol, f"loop-{protocol}")
            )
            if entry is None:
                self.results[protocol] = "缺少配置，未验收"
                continue
            session = f"loop-{protocol}"
            work = self.start(session, [entry])
            if self.cancel_only:
                self.results[protocol] = self.check_live_cancel(protocol, session, work)
                self.finish(session, work, ctrl_c=True)
                self.capture(session, f"ch04-live-{protocol}-cancel-exit")
            elif self.live:
                self.check_live(protocol, session, work)
            else:
                self.check_fixture(protocol, session, work)
        if not self.live:
            self.results["requests"] = [
                {
                    "path": r["path"],
                    "tools": len(r["body"].get("tools", [])),
                    "roles": [m["role"] for m in r["body"]["messages"]],
                }
                for r in self.server.requests
            ]
        output = EVIDENCE / (
            "ch04-live-cancel-results.json"
            if self.cancel_only
            else "ch04-live-results.json"
            if self.live
            else "ch04-tmux-results.json"
        )
        output.write_text(
            json.dumps(self.results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {k: v for k, v in self.results.items() if k != "requests"},
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )


if __name__ == "__main__":
    run = LoopRun(
        live="--live" in sys.argv or "--live-cancel-only" in sys.argv,
        cancel_only="--live-cancel-only" in sys.argv,
    )
    try:
        run.run()
    finally:
        run.close()
