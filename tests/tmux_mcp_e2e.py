"""ch07：真实 tmux、MCP 子进程与双协议对话验收。"""

import json
import shlex
import sys
import uuid
from dataclasses import asdict

import yaml
from test_mcp_manager import FIXTURE, process_exists
from test_tool_protocols import call
from tmux_e2e import EVIDENCE, ROOT
from tmux_permission_e2e import PermissionRun

from termcode.config import load


class McpRun(PermissionRun):
    def __init__(self, live=False):
        super().__init__(live)
        self.mcp = False
        self.missing = False
        self.results["chapter"] = "ch07 MCP"

    def start(self, session, entries):
        work = ROOT / ".cache/tmux-e2e" / self.socket / session
        directory = work / ".termcode"
        directory.mkdir(parents=True, exist_ok=True)
        (work / "home").mkdir(exist_ok=True)
        (directory / "config.yaml").write_text(
            yaml.safe_dump({"providers": entries}), encoding="utf-8"
        )
        if self.mcp:
            servers = {}
            for name, extra in (("demo", []), ("old", ["--legacy"])):
                servers[name] = {
                    "type": "stdio",
                    "command": sys.executable,
                    "args": ["-u", str(FIXTURE), *extra],
                    "env": {
                        "MCP_TEST_PID": str(work / f"{name}.pid"),
                        "MCP_TEST_TRACE": str(work / f"{name}.jsonl"),
                        "MCP_TEST_VALUE": "${MCP_E2E_VALUE}",
                    },
                }
            servers["broken"] = {"type": "stdio", "command": str(work / "no-command")}
            (work / ".termcode.yaml").write_text(
                yaml.safe_dump({"mcp_servers": servers}), encoding="utf-8"
            )
        # Textual 也写 stderr；保留终端属性，仅旁录 MCP 的普通诊断行。
        entry = work / "entry.py"
        entry.write_text(
            "import runpy, sys\nfrom pathlib import Path\n"
            "class Log:\n"
            "    def write(self, text):\n"
            "        if text.startswith('[mcp]'):\n"
            "            with Path('mcp-stderr.txt').open('a') as log:\n"
            "                log.write(text + '\\n')\n"
            "        return sys.__stderr__.write(text)\n"
            "    def __getattr__(self, name):\n"
            "        return getattr(sys.__stderr__, name)\n"
            "sys.stderr = Log()\n"
            "runpy.run_module('termcode', run_name='__main__')\n",
            encoding="utf-8",
        )
        (work / "mcp-stderr.txt").write_text("")
        command = shlex.join([sys.executable, str(entry)])
        runner = work / "run.sh"
        runner.write_text(
            "export LANG=C.UTF-8\nexport TERM=xterm-256color\n"
            f"export HOME={shlex.quote(str(work / 'home'))}\n"
            + (
                "unset MCP_E2E_VALUE\n"
                if self.missing
                else "export MCP_E2E_VALUE=expanded-in-tmux\n"
            )
            + f"stty -g > {shlex.quote(str(work / 'stty-before.txt'))}\n"
            + f"{command}\n"
            + "status=$?\n"
            + f"stty -g > {shlex.quote(str(work / 'stty-after.txt'))}\n"
            + "printf '\\nTERMCODE_EXIT=%s\\n' \"$status\"\n",
            encoding="utf-8",
        )
        self.tmux(
            "new-session",
            "-d",
            "-s",
            session,
            "-x",
            "110",
            "-y",
            "38",
            "-c",
            str(work),
            shlex.join(["sh", str(runner)]),
        )
        self.tmux("set-option", "-t", session, "remain-on-exit", "on")
        self.wait(
            lambda: (
                "Send a message" in self.capture(session)
                and "↑0 ↓0 tok" in self.capture(session)
            ),
            "新的 CLI 空闲界面",
            seconds=180,
        )
        self.capture(session, session + "-start")
        print(f"READY {session}", flush=True)
        return work

    def finish_mcp(self, session, work):
        self.finish(session, work)
        for path in work.glob("*.pid"):
            assert not process_exists(int(path.read_text())), path
        errors = (work / "mcp-stderr.txt").read_text()
        assert "Traceback" not in errors and "close timeout" not in errors
        assert all(secret not in errors for secret in self.secrets if secret)
        (EVIDENCE / f"{session}-mcp-stderr.txt").write_text(errors)

    def scripted_mcp(self, protocol, entry):
        self.mcp = False
        session = f"ch07-{protocol}-empty"
        work = self.start(session, [entry])
        self.begin(session, "你好，请说明你能使用哪些工具。", [{"text": "NO_MCP_DONE"}])
        self.completed(session, "NO_MCP_DONE", session)
        assert len(self.server.requests[-1]["body"]["tools"]) == 6
        assert not (work / "mcp-stderr.txt").read_text()
        self.finish_mcp(session, work)

        self.mcp = True
        self.missing = True
        session = f"ch07-{protocol}-missing"
        work = self.start(session, [entry])
        errors = (work / "mcp-stderr.txt").read_text()
        assert "undefined env var ${MCP_E2E_VALUE}" in errors
        self.begin(
            session,
            "请使用 demo 的只读回显工具返回 missing-env-ok。",
            [
                {"calls": [call("mcp__demo__read_echo", text="missing-env-ok")]},
                {"text": "MISSING_ENV_DONE"},
            ],
        )
        self.completed(session, "MISSING_ENV_DONE", session)
        self.finish_mcp(session, work)

        self.missing = False
        session = f"ch07-{protocol}-mcp"
        work = self.start(session, [entry])
        errors = (work / "mcp-stderr.txt").read_text()
        assert "broken" in errors and "failed" in errors
        assert "protocol=2026-07-28" in errors and "protocol=2025-11-25" in errors
        self.begin(
            session,
            "请调用 demo 的 echo 回显你好 MCP，收到结果后总结。",
            [
                {"calls": [call("mcp__demo__echo", text="你好 MCP")]},
                {"text": "ECHO_DONE"},
            ],
        )
        self.approved(session, ["1"], session + "-once")
        self.completed(session, "ECHO_DONE", session + "-once")
        body = self.server.requests[-1]["body"]
        assert "你好 MCP" in json.dumps(body, ensure_ascii=False)
        assert len(body["tools"]) == 14
        assert not (work / ".termcode/settings.local.yaml").exists()

        self.begin(
            session,
            "再回显 persistent，并永久允许此工具。",
            [
                {"calls": [call("mcp__demo__echo", text="persistent")]},
                {"text": "PERSIST_DONE"},
            ],
        )
        self.approved(session, ["2"], session + "-persist")
        self.completed(session, "PERSIST_DONE", session + "-persist")
        saved = yaml.safe_load((work / ".termcode/settings.local.yaml").read_text())
        assert saved["permissions"]["allow"] == ["mcp__demo__echo"]
        self.finish_mcp(session, work)

        self.tmux("kill-session", "-t", session)
        work = self.start(session, [entry])
        self.begin(
            session,
            "重启后请回显 reloaded。",
            [
                {"calls": [call("mcp__demo__echo", text="reloaded")]},
                {"text": "RELOAD_DONE"},
            ],
        )
        self.completed(session, "RELOAD_DONE", session + "-reload")

        self.begin(
            session,
            "请用旧协议 server 的只读工具读取已展开的环境值。",
            [
                {"calls": [call("mcp__old__read_echo", text="ENV_PROBE")]},
                {"text": "LEGACY_ENV_DONE"},
            ],
        )
        self.completed(session, "LEGACY_ENV_DONE", session + "-legacy-env")
        assert "expanded-in-tmux" in json.dumps(self.server.requests[-1]["body"])

        self.begin(
            session,
            "调用旧 server 的 echo，随后由我取消。",
            [
                {"calls": [call("mcp__old__echo", text="cancelled")]},
            ],
        )
        self.approved(session, ["Escape"], session + "-cancel")
        self.wait(lambda: self.idle(session), "取消回到空闲")
        self.begin(session, "继续聊天。", [{"text": "CANCEL_RESUMED"}])
        self.completed(session, "CANCEL_RESUMED", session + "-cancel-resume")

        self.key(session, "BTab")
        self.key(session, "BTab")
        self.key(session, "BTab")
        self.wait(lambda: "BYPASS" in self.capture(session), "切换 bypass")
        self.begin(
            session,
            "使用旧 server 回显 bypass，随后检查危险命令拒绝。",
            [
                {"calls": [call("mcp__old__echo", text="bypass")]},
                {"calls": [call("bash", command="rm -rf /")]},
                {"text": "BYPASS_BLACKLIST_DONE"},
            ],
        )
        self.completed(session, "BYPASS_BLACKLIST_DONE", session + "-bypass")
        assert "黑名单" in json.dumps(
            self.server.requests[-1]["body"], ensure_ascii=False
        )
        self.finish_mcp(session, work)
        self.results[protocol] = {
            "no_mcp": "passed",
            "stdio_echo_approval": "passed",
            "failed_server_isolation": "passed",
            "permanent_allow_restart": "passed",
            "env_missing_and_expansion": "passed",
            "exit_no_process": "passed",
            "bypass_and_blacklist": "passed",
            "legacy_fallback": "passed",
            "approval_cancel_resume": "passed",
        }

    def live_mcp(self, protocol, entry):
        self.mcp = True
        self.missing = False
        session = f"ch07-live-{protocol}"
        work = self.start(session, [entry])
        marker = "MCP_LIVE_" + uuid.uuid4().hex[:8]
        self.begin(
            session,
            f"请只使用 mcp__demo__echo 工具回显文本 {marker}，"
            "必须实际调用此工具，随后根据工具返回回复相同文本。",
        )
        self.approved(session, ["1"], session)
        self.wait(
            lambda: self.idle(session) and marker in self.capture(session),
            "真实模型调用 MCP 后续答",
            seconds=180,
        )
        self.capture(session, session + "-done")
        trace = [
            json.loads(line) for line in (work / "demo.jsonl").read_text().splitlines()
        ]
        calls = [m for m in trace if m.get("method") == "tools/call"]
        assert any(
            m["params"]["name"] == "echo"
            and marker in m["params"]["arguments"].get("text", "")
            for m in calls
        )
        self.finish_mcp(session, work)
        self.results[protocol] = {"real_model_mcp_echo_approval_reply": "passed"}

    def run(self):
        entries = (
            [asdict(c) for c in load(ROOT / ".termcode/config.yaml").providers]
            if self.live
            else []
        )
        self.secrets = [entry["api_key"] for entry in entries]
        for protocol in ("openai", "anthropic"):
            entry = (
                next((e for e in entries if e["protocol"] == protocol), None)
                if self.live
                else self.entry(protocol, "mcp-test")
            )
            if entry is None:
                self.results[protocol] = "缺少真实模型配置，未验收"
                continue
            (self.live_mcp if self.live else self.scripted_mcp)(protocol, entry)


if __name__ == "__main__":
    run = McpRun(live="--live" in sys.argv)
    try:
        run.run()
    finally:
        path = EVIDENCE / (
            "ch07-live-results.json" if run.live else "ch07-tmux-results.json"
        )
        path.write_text(
            json.dumps(run.results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(run.results, ensure_ascii=False), flush=True)
        run.close()
