"""在真实 tmux 终端运行 CLI；仅使用本地 SSE 服务，不调用云端模型。"""

import json
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

import yaml
from conftest import SSEServer

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/evidence"


class TmuxRun:
    def __init__(self) -> None:
        self.socket = f"termcode-e2e-{uuid.uuid4().hex[:10]}"
        self.server = SSEServer()
        self.server.start()
        EVIDENCE.mkdir(exist_ok=True)
        self.results: dict[str, object] = {"backend": "local SSE fixture", "cases": []}
        self._last_capture = ""
        self._last_session = ""

    def tmux(self, *args: str, check: bool = True) -> str:
        result = subprocess.run(
            ["tmux", "-u", "-L", self.socket, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=check,
            timeout=10,
        )
        return result.stdout

    def capture(self, session: str, name: str | None = None) -> str:
        text = self.tmux("capture-pane", "-p", "-S", "-", "-t", session)
        self._last_capture = text
        self._last_session = session
        if name:
            (EVIDENCE / f"tmux-{name}.txt").write_text(text, encoding="utf-8")
        assert "TEST_SECRET" not in text, "终端中出现测试密钥"
        assert "HIDDEN_THINKING" not in text, "终端中出现思考内容"
        return text

    def wait(self, predicate, description: str, seconds: float = 30) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        if self._last_session:
            self.capture(self._last_session)
        (EVIDENCE / f"tmux-{self._last_session}-failure.txt").write_text(
            self._last_capture, encoding="utf-8"
        )
        raise AssertionError(f"等待超时：{description}\n{self._last_capture}")

    def entry(self, protocol: str, name: str) -> dict:
        return {
            "name": name,
            "protocol": protocol,
            "model": "test-model",
            "api_key": "TEST_SECRET",
            "base_url": self.server.url + ("/v1" if protocol == "openai" else ""),
            "thinking": protocol == "anthropic",
        }

    def start(self, session: str, entries: list[dict]) -> Path:
        work = ROOT / ".cache/tmux-e2e" / self.socket / session
        (work / ".termcode").mkdir(parents=True)
        (work / ".termcode/config.yaml").write_text(
            yaml.safe_dump({"providers": entries}), encoding="utf-8"
        )
        before = shlex.quote(str(work / "stty-before.txt"))
        after = shlex.quote(str(work / "stty-after.txt"))
        command = shlex.join([sys.executable, "-m", "termcode"])
        runner = work / "run.sh"
        runner.write_text(
            f"export LANG=C.UTF-8\nexport TERM=xterm-256color\n"
            f"stty -g > {before}\n{command}\nstatus=$?\nstty -g > {after}\n"
            "printf '\\nTERMCODE_EXIT=%s\\n' \"$status\"\n",
            encoding="utf-8",
        )
        self.tmux(
            "new-session",
            "-d",
            "-s",
            session,
            "-x",
            "100",
            "-y",
            "36",
            "-c",
            str(work),
            shlex.join(["sh", str(runner)]),
        )
        self.tmux("set-option", "-t", session, "remain-on-exit", "on")
        marker = "选择供应商" if len(entries) > 1 else "Send a message"
        self.wait(
            lambda: (
                marker in self.capture(session)
                and (len(entries) > 1 or "v0.1.0" in self.capture(session))
            ),
            "启动界面",
            seconds=120,
        )
        self.capture(session, f"{session}-start")
        print(f"READY {session}", flush=True)
        return work

    def send(self, session: str, text: str) -> None:
        self.tmux("send-keys", "-t", session, "-l", "--", text)

    def key(self, session: str, key: str) -> None:
        self.tmux("send-keys", "-t", session, key)

    def turn(self, session: str, text: str) -> None:
        previous = len(self.server.requests)
        self.send(session, text)
        self.key(session, "Enter")
        self.wait(lambda: len(self.server.requests) == previous + 1, "发送模型请求")
        self.wait(
            lambda: (
                "Imagining" not in self.capture(session)
                and "完成 ·" in self.capture(session)
            ),
            "Markdown 定型",
        )

    def finish(self, session: str, work: Path, ctrl_c: bool = False) -> None:
        if ctrl_c:
            self.key(session, "C-c")
        else:
            self.send(session, "/exit")
            self.key(session, "Enter")
        self.wait(lambda: "TERMCODE_EXIT=" in self.capture(session), "退出恢复终端")
        final = self.capture(session, f"{session}-exit")
        assert "TERMCODE_EXIT=0" in final
        before = (work / "stty-before.txt").read_text()
        after = (work / "stty-after.txt").read_text()
        assert before == after, "退出后终端模式没有恢复"
        self.results["cases"].append(
            {"scenario": session, "exit_code": 0, "terminal_restored": True}
        )

    def run(self) -> None:
        self.server.delay = 0.08
        self.server.gate.clear()
        work = self.start("anthropic", [self.entry("anthropic", "local-claude")])
        assert "v0.1.0" in self.capture("anthropic")
        self.send("anthropic", "请记住我的名字叫小林。")
        self.key("anthropic", "M-Enter")
        self.send("anthropic", "请输出带列表和代码块的问候。")
        self.key("anthropic", "Enter")
        time.sleep(0.2)
        self.capture("anthropic", "anthropic-submitted")
        self.wait(lambda: bool(self.server.requests), "首轮请求")
        time.sleep(1.2)
        waiting = self.capture("anthropic", "anthropic-waiting")
        assert (
            "Imagining… (1s · 第 1 轮)" in waiting
            or "Imagining… (2s · 第 1 轮)" in waiting
        )
        assert "\n" in self.server.requests[0]["body"]["messages"][0]["content"]
        self.server.gate.set()
        self.wait(lambda: "**你好**" in self.capture("anthropic"), "纯文本流式增量")
        self.capture("anthropic", "anthropic-streaming")
        self.wait(lambda: "完成 ·" in self.capture("anthropic"), "首轮完成")
        self.capture("anthropic", "anthropic-markdown")
        self.server.delay = 0.005
        self.turn("anthropic", "我叫什么名字？")
        history = self.server.requests[-1]["body"]["messages"]
        assert [message["role"] for message in history] == ["user", "assistant", "user"]
        assert "小林" in history[0]["content"]
        for index in range(5):
            self.turn("anthropic", f"历史滚动测试第 {index} 轮")
        self.key("anthropic", "PPage")
        self.capture("anthropic", "anthropic-history")
        self.tmux("resize-window", "-t", "anthropic", "-x", "38", "-y", "20")
        time.sleep(0.2)
        self.capture("anthropic", "anthropic-narrow")
        self.finish("anthropic", work)
        final = self.capture("anthropic")
        assert "小林" in final and "历史滚动测试第 4 轮" in final

        work = self.start("openai", [self.entry("openai", "local-openai")])
        self.turn("openai", "请输出一个 Python 代码块。")
        assert len(self.server.requests[-1]["body"]["messages"]) == 2
        self.capture("openai", "openai-markdown")
        self.finish("openai", work)

        work = self.start(
            "selection",
            [
                self.entry("anthropic", "first-claude"),
                self.entry("openai", "second-openai"),
            ],
        )
        self.key("selection", "Down")
        self.key("selection", "Enter")
        self.wait(lambda: "second-openai" in self.capture("selection"), "选中第二项")
        self.turn("selection", "你好，请回复。")
        assert self.server.requests[-1]["path"] == "/v1/chat/completions"
        self.capture("selection", "selection-selected")
        self.finish("selection", work)

        work = self.start("error", [self.entry("openai", "local-errors")])
        self.server.mode = "auth_error"
        self.send("error", "触发认证错误")
        self.key("error", "Enter")
        self.wait(lambda: "认证失败" in self.capture("error"), "可恢复错误")
        self.capture("error", "error-authentication")
        self.server.mode = "normal"
        self.turn("error", "恢复后的正常请求")
        assert len(self.server.requests[-1]["body"]["messages"]) == 4
        self.capture("error", "error-recovered")
        self.finish("error", work)

        self.server.gate.clear()
        work = self.start("ctrl-c", [self.entry("openai", "local-cancel")])
        self.send("ctrl-c", "等待模型期间取消")
        self.key("ctrl-c", "Enter")
        self.wait(lambda: "Imagining" in self.capture("ctrl-c"), "等待期间 Ctrl+C")
        self.key("ctrl-c", "C-c")
        self.wait(
            lambda: (
                "已取消" in self.capture("ctrl-c")
                and "Imagining" not in self.capture("ctrl-c")
            ),
            "取消且留在会话",
        )
        assert "TERMCODE_EXIT=" not in self.capture("ctrl-c")
        self.server.gate.set()
        self.turn("ctrl-c", "取消后继续对话")
        self.finish("ctrl-c", work, ctrl_c=True)

        requests = []
        for request in self.server.requests:
            body = request["body"]
            requests.append(
                {
                    "path": request["path"],
                    "model": body["model"],
                    "stream": body["stream"],
                    "thinking": body.get("thinking"),
                    "roles": [message["role"] for message in body["messages"]],
                }
            )
        self.results["requests"] = requests
        (EVIDENCE / "tmux-results.json").write_text(
            json.dumps(self.results, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def close(self) -> None:
        self.server.close()
        self.tmux("kill-server", check=False)


if __name__ == "__main__":
    run = TmuxRun()
    try:
        run.run()
        print(json.dumps(run.results, ensure_ascii=False, indent=2))
    finally:
        run.close()
