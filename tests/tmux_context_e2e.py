"""ch08：真实 tmux 中的双协议压缩验收及可选真实模型验收。"""

import argparse
import json
import re
import shlex
from dataclasses import asdict

from test_tool_protocols import call
from tmux_e2e import EVIDENCE, ROOT, TmuxRun

from termcode.compact.summary_prompt import SUMMARY_TITLES
from termcode.config import load

SUMMARY = (
    "<analysis>不展示的草稿</analysis><summary>"
    + "\n".join(SUMMARY_TITLES)
    + "</summary>"
)


class ContextRun(TmuxRun):
    def __init__(self, live=False, restart_only=False):
        super().__init__()
        self.live = live
        self.restart_only = restart_only
        self.secrets = []
        self._bootstrapped = False
        self.server.delay = 0.01
        self.results["backend"] = "configured live model" if live else "local HTTP/SSE"

    def capture(self, session, name=None):
        text = super().capture(session, name)
        assert all(secret not in text for secret in self.secrets if secret)
        return text

    def start(self, session, entries):
        # 只向独立 tmux 服务注入环境，隔离用户级 MCP 与权限配置。
        work = ROOT / ".cache/tmux-e2e" / self.socket / session
        (work / "home").mkdir(parents=True, exist_ok=True)
        if not self._bootstrapped:
            self.tmux("new-session", "-d", "-s", "bootstrap", "sleep 3600")
            self._bootstrapped = True
        self.tmux("set-environment", "-g", "HOME", str(work / "home"))
        return super().start(session, entries)

    def begin(self, session, text, responses=None):
        if responses is not None:
            self.server.responses = list(responses)
        self.send(session, text)
        self.key(session, "Enter")

    def idle(self, session):
        pane = self.capture(session)
        return "Send a message" in pane and all(
            word not in pane for word in ("Imagining", "Running…", "是否继续?")
        )

    def completed(self, session, marker):
        def completed_marker():
            pane = self.capture(session)
            if marker.startswith("CH08_LIVE_"):
                # 输入框和用户消息也包含代号，只接受助手单独输出的完成行。
                return (
                    re.search(r"(?m)^\s*" + re.escape(marker) + r"\s*$", pane)
                    is not None
                )
            return marker in pane

        self.wait(
            lambda: completed_marker() and self.idle(session),
            marker,
            seconds=180,
        )
        self.capture(session, session + "-" + marker)

    def notice_count(self, session):
        return len(re.findall(r"已压缩，token 从 \d+ 降至 \d+", self.capture(session)))

    def wait_compact(self, session, previous, name):
        self.wait(
            lambda: self.notice_count(session) > previous and self.idle(session),
            name,
            seconds=180,
        )
        self.capture(session, session + "-" + name)

    def scripted(self, protocol):
        session = "ch08-" + protocol
        entry = self.entry(protocol, protocol)
        entry["context_window"] = 80000
        work = self.start(session, [entry])
        path = work / "large.txt"
        path.write_text("L" * 80000, encoding="utf-8")
        self.begin(
            session,
            "请用 read_file 读取 large.txt，完成后确认。",
            [
                {"calls": [call("read_file", id="large-A", path="large.txt")]},
                {"text": "LARGE_DONE"},
            ],
        )
        self.completed(session, "LARGE_DONE")
        spills = list((work / ".termcode/sessions").glob("*/tool-results/large-A"))
        assert len(spills) == 1 and spills[0].stat().st_size == 80008
        body = self.server.requests[-1]["body"]
        raw = json.dumps(body, ensure_ascii=False)
        assert "[content offloaded]" in raw and "original size: 80008" in raw
        assert "文件读取工具" in raw and "不要凭头部预览猜测" in raw

        before = len(self.server.requests)
        self.begin(session, "/unknown")
        self.completed(session, "未知命令")
        assert len(self.server.requests) == before
        self.begin(session, "/plan")
        self.completed(session, "已进入计划模式")
        self.begin(session, "请检查工具列表并回复。", [{"text": "PLAN_DONE"}])
        self.completed(session, "PLAN_DONE")
        tools = self.server.requests[-1]["body"]["tools"]
        assert len(tools) == 3
        self.begin(session, "/do", [{"text": "DO_DONE"}])
        self.completed(session, "DO_DONE")

        previous = self.notice_count(session)
        before = len(self.server.requests)
        self.begin(session, "/compact", [{"text": SUMMARY, "delay": 0.3}])
        self.wait_compact(session, previous, "manual")
        assert len(self.server.requests) == before + 1
        assert not self.server.requests[-1]["body"].get("tools")

        self.begin(
            session,
            "请记住下一轮继续检查。",
            [{"text": "ANCHOR_DONE", "input_tokens": 50000}],
        )
        self.completed(session, "ANCHOR_DONE")
        previous = self.notice_count(session)
        before = len(self.server.requests)
        self.begin(
            session,
            "继续检查并回复。",
            [{"text": SUMMARY, "delay": 1}, {"text": "AUTO_DONE"}],
        )
        self.wait(lambda: len(self.server.requests) > before, "自动摘要请求")
        assert "正在压缩上下文..." in self.capture(session, session + "-auto-before")
        assert not self.server.requests[before]["body"].get("tools")
        self.completed(session, "AUTO_DONE")
        assert self.notice_count(session) > previous
        normal = self.server.requests[-1]["body"]
        combined = normal["messages"][1 if protocol == "openai" else 0]["content"]
        assert all(title in combined for title in SUMMARY_TITLES)
        assert all(
            title in combined
            for title in ("最近读过的文件", "当前可用工具", "边界提示")
        )

        before = len(self.server.requests)
        self.begin(
            session,
            "请继续完成检查。",
            [
                {
                    "status": 400,
                    "body": {
                        "error": {
                            "type": "invalid_request_error",
                            "code": "context_length_exceeded",
                            "message": "prompt is too long",
                        }
                    },
                },
                {"text": SUMMARY, "delay": 1},
                {"text": "EMERGENCY_DONE"},
            ],
        )
        self.wait(
            lambda: "上下文撞墙，自动压缩中..." in self.capture(session), "紧急压缩提示"
        )
        self.capture(session, session + "-emergency-before")
        self.completed(session, "EMERGENCY_DONE")
        assert len(self.server.requests) == before + 3
        self.finish(session, work)
        assert spills[0].exists()
        self.results[protocol] = {
            "large_result_bytes": 80008,
            "manual": "passed",
            "auto": "passed",
            "emergency": "passed",
            "slash_commands": "passed",
            "session_retained": True,
        }

    def real_model(self):
        cfg = next(
            c
            for c in load(ROOT / ".termcode/config.yaml").providers
            if c.protocol == "anthropic"
        )
        self.secrets = [cfg.api_key]
        entry = asdict(cfg)
        entry.update(context_window=80000, thinking=False)
        session = "ch08-live-anthropic"
        work = self.start(session, [entry])
        (work / "large.txt").write_text("large sample " * 6200, encoding="utf-8")
        self.begin(
            session,
            "请仅使用 read_file 读取当前目录的 large.txt。"
            "完成后只回复 CH08_LIVE_READ_OK。不要使用其他工具。",
        )
        self.completed(session, "CH08_LIVE_READ_OK")
        spills = list((work / ".termcode/sessions").glob("*/tool-results/*"))
        assert spills and max(p.stat().st_size for p in spills) > 80000
        previous = self.notice_count(session)
        self.begin(session, "/compact")
        self.wait_compact(session, previous, "live-manual")
        self.begin(session, "/unknown")
        self.completed(session, "未知命令")
        self.begin(session, "/plan")
        self.completed(session, "已进入计划模式")
        self.begin(session, "/do")
        self.wait(
            lambda: not self.idle(session),
            "执行命令开始",
            seconds=180,
        )
        self.wait(lambda: self.idle(session), "执行命令后恢复空闲", seconds=180)

        old_notices = set(
            re.findall(r"已压缩，token 从 \d+ 降至 \d+", self.capture(session))
        )
        auto_notices = set()
        for i in range(1, 11):
            path = work / f"context-{i}.txt"
            path.write_text(
                ("alpha beta gamma delta " * 1900) + str(i), encoding="utf-8"
            )
            self.begin(
                session,
                f"请仅用 read_file 读取 context-{i}.txt，一次完整读取。"
                f"完成后仅回复 CH08_LIVE_TURN_{i}_OK。",
            )
            self.completed(session, f"CH08_LIVE_TURN_{i}_OK")
            print(f"LIVE_TURN {i} completed", flush=True)
            auto_notices = (
                set(re.findall(r"已压缩，token 从 \d+ 降至 \d+", self.capture(session)))
                - old_notices
            )
            if auto_notices:
                break
        assert auto_notices, "真实模型未触发自动压缩"
        self.capture(session, session + "-live-auto")
        self.finish(session, work)
        assert all(p.exists() for p in spills)
        self.results["live"] = {
            "read_large_file": True,
            "manual": True,
            "auto": True,
            "commands": True,
            "session_retained": True,
            "auto_notices": sorted(auto_notices),
        }

    def restart_check(self):
        work = self.start("ch08-restart-first", [self.entry("openai", "restart")])
        directory = work / ".termcode/sessions"
        before = {p.name for p in directory.iterdir()}
        assert len(before) == 1
        self.finish("ch08-restart-first", work)
        self.tmux(
            "new-session",
            "-d",
            "-s",
            "ch08-restart-second",
            "-x",
            "100",
            "-y",
            "36",
            "-c",
            str(work),
            shlex.join(["sh", str(work / "run.sh")]),
        )
        self.tmux("set-option", "-t", "ch08-restart-second", "remain-on-exit", "on")
        self.wait(
            lambda: self.idle("ch08-restart-second"),
            "在同一工作区重新启动",
            seconds=120,
        )
        after = {p.name for p in directory.iterdir()}
        assert before < after and len(after) == 2
        self.finish("ch08-restart-second", work)
        assert {p.name for p in directory.iterdir()} == after
        self.results["restart"] = {"same_workspace": True, "session_ids": sorted(after)}

    def run(self):
        try:
            if self.restart_only:
                self.restart_check()
            elif self.live:
                self.real_model()
            else:
                for protocol in ("anthropic", "openai"):
                    self.scripted(protocol)
        finally:
            name = (
                "ch08-tmux-restart-results.json"
                if self.restart_only
                else "ch08-tmux-live-results.json"
                if self.live
                else "ch08-tmux-results.json"
            )
            (EVIDENCE / name).write_text(
                json.dumps(self.results, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.tmux("kill-server", check=False)
            self.server.close()
        print(json.dumps(self.results, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--restart-only", action="store_true")
    args = parser.parse_args()
    ContextRun(args.live, args.restart_only).run()
