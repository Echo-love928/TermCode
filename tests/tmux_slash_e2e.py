"""ch10：真实 tmux、真实 CLI 和工具执行，模型响应使用本地 HTTP/SSE。"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys

import yaml
from tmux_e2e import EVIDENCE, ROOT, TmuxRun

from termcode.command.builtin_prompt import REVIEW_DIRECTIVE
from termcode.compact.summary_prompt import SUMMARY_TITLES
from termcode.config import load
from termcode.prompt import EXECUTE_DIRECTIVE
from termcode.session import load_session, load_usage

SESSION = "ch10-slash"
NAMES = (
    "clear compact do exit help memory permission plan resume review "
    "session skill status"
).split()
SUMMARY = "<summary>" + "\n".join(SUMMARY_TITLES) + "</summary>"
STATUS_KEYS = ["Mode", "Tokens", "Tools", "Memories", "Model", "Directory"]


class SlashRun(TmuxRun):
    def __init__(self, live=False):
        super().__init__()
        self.live = live
        self.secrets = []
        self.prefix = "ch10-live" if live else "ch10"
        self.results["backend"] = "configured live model" if live else "local HTTP/SSE"

    def capture(self, session, name=None):
        pane = super().capture(session, name)
        assert all(secret not in pane for secret in self.secrets)
        return pane

    def pane(self):
        pane = self.tmux("capture-pane", "-p", "-t", SESSION)
        assert all(secret not in pane for secret in self.secrets)
        return pane

    def evidence(self, name):
        pane = self.pane()
        assert "TEST_SECRET" not in pane
        path = EVIDENCE / f"{self.prefix}-{name}.txt"
        path.write_text(pane, encoding="utf-8")
        self.results["cases"].append(name)
        print(f"PASS {name}", flush=True)
        return pane

    def until(self, predicate, label, seconds=30):
        self.wait(lambda: predicate(self.capture(SESSION)), label, seconds=seconds)

    def command(self, text):
        self.send(SESSION, text)
        self.key(SESSION, "Enter")

    def start_cli(self):
        work = ROOT / ".cache/tmux-e2e" / self.socket / SESSION
        home = work / "home"
        memory = home / ".termcode/memory"
        memory.mkdir(parents=True)
        (memory / "MEMORY.md").write_text("隔离验收用记忆正文\n", encoding="utf-8")
        (work / ".termcode").mkdir()
        config = work / ".termcode/config.yaml"
        if self.live:
            source = ROOT / ".termcode/config.yaml"
            self.secrets = [provider.api_key for provider in load(source).providers]
            # 真实模型只能读取隔离工作区，拒绝执行命令或写入文件。
            (work / ".termcode/settings.yaml").write_text(
                yaml.safe_dump(
                    {"permissions": {"deny": ["Bash", "Write", "Edit", "Glob", "Grep"]}}
                ),
                encoding="utf-8",
            )
        else:
            config.write_text(
                yaml.safe_dump({"providers": [self.entry("openai", "ch10-local")]}),
                encoding="utf-8",
            )
        (work / "sample.txt").write_text("核对码：SLASH_READ_42\n", encoding="utf-8")
        # Textual 的 inline 驱动写到 __stderr__；单独分流，仍运行正式 CLI。
        launch = (
            "import runpy, sys; sys.__stderr__ = sys.stdout; "
            "runpy.run_module('termcode', run_name='__main__')"
        )
        if self.live:
            # 只选择配置中的首个供应商，不复制密钥或改写原始配置。
            launch = (
                "import termcode.config as config; original_load = config.load; "
                "config.load = lambda path: "
                f"config.Config(original_load({str(source)!r}).providers[:1]); "
                + launch
            )
        command = shlex.join([sys.executable, "-c", launch])
        runner = work / "run.sh"
        runner.write_text(
            "export LANG=C.UTF-8 TERM=xterm-256color\n"
            f"export HOME={shlex.quote(str(home))}\n"
            f"export PYTHONPATH={shlex.quote(str(ROOT / 'src'))}\n"
            "stty -g > stty-before.txt\n"
            f"{command} 2> termcode.err\n"
            "status=$?\n"
            "stty -g > stty-after.txt\n"
            'printf "%s\\n" "$status" > exit-code.txt\n',
            encoding="utf-8",
        )
        self.tmux("new-session", "-d", "-s", "bootstrap", "sleep 600")
        self.tmux(
            "new-session",
            "-d",
            "-s",
            SESSION,
            "-x",
            "200",
            "-y",
            "50",
            "-c",
            str(work),
            shlex.join(["sh", str(runner)]),
        )
        self.until(
            lambda p: "Send a message" in p and "DEFAULT" in p,
            "A1 启动",
            seconds=120,
        )
        self.evidence("A1-start")
        return work

    def local_commands(self):
        self.command("/help")
        self.until(lambda p: "/status" in p and "查看运行状态和用量" in p, "A2 帮助")
        pane = self.evidence("A2-help")
        rows = re.findall(r"(?m)^\s*/([a-z]+) {2,}\S", pane)
        assert rows == NAMES, rows
        self.command("/status")
        self.until(lambda p: "Directory:" in p, "B1 状态")
        pane = self.evidence("B1-status")
        assert re.findall(r"(?m)^\s*(\w+):\s", pane)[-6:] == STATUS_KEYS
        assert re.search(r"Mode:\s+default", pane)
        assert "0 in / 0 out" in pane
        self.command("/permission")
        self.until(lambda p: re.search(r"(?m)^\s*default\s*$", p), "B2 权限")
        self.evidence("B2-permission")
        self.command("/memory")
        self.until(lambda p: "MEMORY.md" in p, "B3 记忆")
        pane = self.evidence("B3-memory")
        assert "隔离验收用记忆正文" not in pane
        self.command("/session")
        self.until(lambda p: "Session:" in p and "conversation.jsonl" in p, "B4 会话")
        pane = self.evidence("B4-B5-session")
        assert "↑0 ↓0 tok" in pane and not self.server.requests

    @staticmethod
    def candidates(pane):
        return re.findall(r"(?m)^\s*[↑↓›]?\s*/([a-z]+) {2,}\S", pane.rsplit("╯", 1)[-1])

    def completion(self):
        self.send(SESSION, "/")
        self.until(lambda p: self.candidates(p) == NAMES[:8], "C1 首屏候选")
        self.evidence("C1-first-eight")
        for _ in range(len(NAMES) - 1):
            self.key(SESSION, "Down")
        self.until(lambda p: self.candidates(p) == NAMES[-8:], "C1 滚动候选")
        self.evidence("C1-scroll")
        self.send(SESSION, "s")
        matches = ["session", "skill", "status"]
        self.until(lambda p: self.candidates(p) == matches, "C2 前缀")
        self.evidence("C2-prefix")
        # 保留滚动前已选中的 status，再用上下键验证高亮可切换。
        self.key(SESSION, "Up")
        self.key(SESSION, "Up")
        self.until(lambda p: "› /session" in p.split("❯")[-1], "C3 向上")
        self.key(SESSION, "Down")
        self.key(SESSION, "Down")
        self.until(lambda p: "› /status" in p.split("❯")[-1], "C3 向下")
        self.evidence("C3-highlight")
        self.key(SESSION, "Enter")
        self.until(
            lambda p: not self.candidates(p) and "Send a message" in p, "C4 回车"
        )
        assert "Directory:" in self.evidence("C4-enter")
        self.send(SESSION, "/s")
        self.until(lambda p: self.candidates(p) == matches, "C5 补全")
        self.key(SESSION, "Escape")
        self.until(lambda p: not self.candidates(p) and "❯  /s" in p, "C5 取消")
        self.evidence("C5-escape")
        self.key(SESSION, "BSpace")
        self.key(SESSION, "BSpace")
        self.until(
            lambda p: "Send a message" in p and not self.candidates(p), "C6 删除"
        )
        self.evidence("C6-backspace")
        self.send(SESSION, "/s")
        self.until(lambda p: self.candidates(p) == matches, "C7 补全")
        self.key(SESSION, "Tab")
        self.until(lambda p: "Session:" in p and not self.candidates(p), "C7 Tab")
        self.evidence("C7-tab")
        assert not self.server.requests

    def conversation(self, work):
        sessions = work / ".termcode/sessions"
        old = next(sessions.iterdir())
        self.command("/plan")
        self.until(lambda p: re.search(r"(?m)^\s*PLAN\s+test-model", p), "D1 计划")
        self.evidence("D1-plan")
        self.server.responses = [
            {
                "calls": [
                    {
                        "id": "read-sample",
                        "name": "read_file",
                        "input": json.dumps({"path": "sample.txt"}),
                    }
                ]
            },
            {"text": "已读取核对码 SLASH_READ_42。计划只返回核对码，不修改文件。"},
        ]
        self.command("请用 read_file 读取 sample.txt，再拟定返回核对码的计划。")
        self.until(lambda p: "计划只返回核对码" in p and "完成 ·" in p, "真实工具回合")
        pane = self.evidence("D1-read-file")
        assert "read_file(" in pane
        tool_reply = self.server.requests[1]["body"]["messages"]
        assert any(
            m["role"] == "tool" and "SLASH_READ_42" in m["content"] for m in tool_reply
        )

        self.server.responses = [
            {"text": "按计划返回：SLASH_READ_42", "input_tokens": 14000}
        ]
        self.command("/do")
        self.until(lambda p: "按计划返回" in p and "DEFAULT" in p, "D2 执行")
        self.evidence("D2-do")
        self.wait(
            lambda: any(
                m.content == "按计划返回：SLASH_READ_42" for m in load_session(str(old))
            ),
            "D2 存档",
        )
        assert any(
            m.role == "user" and m.content == EXECUTE_DIRECTIVE
            for m in load_session(str(old))
        )
        prior_usage = load_usage(str(old))
        assert prior_usage[0] > 0 and prior_usage[1] > 0

        self.server.responses = [{"text": SUMMARY, "delay": 0.8}]
        self.command("/compact")
        self.until(lambda p: "正在压缩上下文" in p, "D3 压缩进度")
        self.evidence("D3-compact-progress")
        self.until(lambda p: "已压缩，token 从" in p, "D3 压缩完成")
        pane = self.evidence("D3-compact-result")
        match = re.search(r"token 从 (\d+) 降至 (\d+)", pane)
        assert match and int(match[1]) > int(match[2]), pane
        assert load_usage(str(old)) == prior_usage
        assert any(
            m.content.startswith("## 历史会话摘要") for m in load_session(str(old))
        )

        self.command("/clear")
        self.until(lambda p: "已清空当前会话" in p and "↑0 ↓0 tok" in p, "D4 清空")
        pane = self.evidence("D4-clear")
        assert "按计划返回" not in pane and "SLASH_READ_42" not in pane
        after = list(sessions.iterdir())
        assert len(after) == 2 and old in after
        new = next(path for path in after if path != old)
        assert load_session(str(new)) == []

        self.server.responses = [{"text": "审查完成：核对当前上下文。", "delay": 0.8}]
        self.command("/review")
        self.until(lambda p: "Imagining" in p, "E1 流式")
        self.evidence("E1-review-stream")
        self.until(lambda p: "审查完成：核对当前上下文" in p, "E1 回复")
        self.evidence("E1-review-reply")
        self.wait(lambda: len(load_session(str(new))) == 2, "E1 存档")
        stored = load_session(str(new))
        assert stored[0].role == "user" and stored[0].content == REVIEW_DIRECTIVE
        assert len(self.server.requests[-1]["body"]["messages"]) == 2

        self.command("/resume")
        self.until(lambda p: "恢复会话 · 2 项" in p, "F1 会话列表")
        self.evidence("F1-resume-list")
        self.key(SESSION, "Down")
        self.key(SESSION, "Enter")
        self.until(lambda p: f"已恢复会话 {old.name}" in p, "F1 恢复旧会话")
        assert "按计划返回" in self.evidence("F1-resume-selected")
        self.command("/status")
        self.until(
            lambda p: f"{prior_usage[0]} in / {prior_usage[1]} out" in p, "F1 恢复用量"
        )
        self.evidence("F1-resume-usage")
        return old

    def invalid_inputs(self, old):
        before = (old / "conversation.jsonl").read_bytes()
        requests = len(self.server.requests)
        pane = self.evidence("G1-baseline")
        usage = re.findall(r"↑[^\s]+ ↓[^\s]+ tok", pane)[-1]
        self.command("/foobar")
        self.until(lambda p: "未知命令" in p, "G1 未知命令")
        pane = self.evidence("G1-unknown")
        assert "/help" in pane and usage in pane
        assert (old / "conversation.jsonl").read_bytes() == before
        self.key(SESSION, "Enter")
        self.send(SESSION, "   ")
        self.key(SESSION, "Enter")
        # 随后的本地查询是队列屏障，确保前面的回车已经处理完毕。
        self.command("/permission")
        self.until(lambda p: re.search(r"(?m)^\s*default\s*$", p), "G2 空回车后仍可用")
        self.evidence("G2-empty-and-whitespace")
        assert (old / "conversation.jsonl").read_bytes() == before
        assert len(self.server.requests) == requests

    def startup_collision(self, work):
        # 在独立进程的启动入口注入重复注册，避免修改主工作区源码。
        script = """
import termcode.command as commands
from termcode.cli import main
register = commands.register_builtins
def duplicate(reg):
    register(reg)
    reg.register(reg.lookup('help'))
commands.register_builtins = duplicate
main()
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=work,
            capture_output=True,
            encoding="utf-8",
            timeout=45,
            check=False,
            env=os.environ
            | {"HOME": str(work / "home"), "PYTHONPATH": str(ROOT / "src")},
        )
        assert result.returncode != 0
        assert "RuntimeError: command conflict: help" in result.stderr, result.stderr
        assert "Send a message" not in result.stdout
        (EVIDENCE / "ch10-H1-startup-conflict.txt").write_text(
            result.stderr, encoding="utf-8"
        )
        self.results["cases"].append("H1-startup-conflict")
        print("PASS H1-startup-conflict", flush=True)

    def live_conversation(self, work):
        path = work / "sample.py"
        original = b"# CH10_LIVE_CODE_285\ndef next_value(n):\n    return n - 1\n"
        path.write_bytes(original)
        self.command(
            "请只用 read_file 读取 sample.py，告诉我 next_value 的实际行为，"
            "并在回复里引用文件中的核对码。不要修改文件。"
        )
        archive = next((work / ".termcode/sessions").iterdir())

        def read_completed():
            messages = load_session(str(archive))
            return (
                any(msg.role == "tool" for msg in messages)
                and messages[-1].role == "assistant"
                and "CH10_LIVE_CODE_285" in messages[-1].content
            )

        self.wait(read_completed, "真实模型读取文件并回复", seconds=180)
        self.until(lambda p: "read_file(" in p and "完成 ·" in p, "真实工具回显")
        self.evidence("read-file")
        messages = load_session(str(archive))
        assert any(
            call.name == "read_file" for msg in messages for call in msg.tool_calls
        )
        self.command("/review")
        self.wait(
            lambda: any(
                msg.role == "user" and msg.content == REVIEW_DIRECTIVE
                for msg in load_session(str(archive))[len(messages) :]
            ),
            "真实审查请求已存档",
        )
        self.evidence("review-start")

        def review_completed():
            updated = load_session(str(archive))
            return (
                len(updated) > len(messages) + 1
                and updated[len(messages)].role == "user"
                and updated[len(messages)].content == REVIEW_DIRECTIVE
                and updated[-1].role == "assistant"
                and not updated[-1].tool_calls
                and bool(updated[-1].content)
            )

        self.wait(review_completed, "真实模型回复审查命令", seconds=180)
        self.until(
            lambda p: (
                "完成 ·" in p
                and not any(
                    marker in p for marker in ("Imagining", "Running…", "是否继续?")
                )
            ),
            "审查回显",
        )
        assert path.read_bytes() == original
        self.evidence("review")

    def run(self):
        work = self.start_cli()
        if self.live:
            self.live_conversation(work)
        else:
            self.local_commands()
            self.completion()
            old = self.conversation(work)
            self.invalid_inputs(old)
            self.startup_collision(work)
        self.evidence("exit-before")
        self.command("/exit")
        self.wait(
            lambda: (
                SESSION
                not in self.tmux("list-sessions", "-F", "#{session_name}").splitlines()
            ),
            "退出 tmux 会话",
        )
        assert (work / "exit-code.txt").read_text().strip() == "0"
        assert (work / "stty-before.txt").read_bytes() == (
            work / "stty-after.txt"
        ).read_bytes()
        assert (work / "termcode.err").read_bytes() == b""
        self.results.update(
            exit_code=0,
            stderr_lines=0,
            terminal_restored=True,
            requests=len(self.server.requests),
        )
        self.results["cases"].append("exit-and-stderr")
        print("PASS exit-and-stderr", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="使用当前项目配置的首个模型"
    )
    run = SlashRun(live=parser.parse_args().live)
    try:
        run.run()
    finally:
        (EVIDENCE / f"{run.prefix}-tmux-results.json").write_text(
            json.dumps(run.results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        run.close()
