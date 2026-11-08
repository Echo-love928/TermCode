"""在真实 tmux 中验收 ch12，可选使用项目已配置的真实模型。"""

import argparse
import json
import shlex
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml
from tmux_e2e import EVIDENCE, ROOT, TmuxRun

BLOCKED = "blocked by hook"
REMINDER = "默认用 zh-CN 回复"
DELETE = "请帮我 delete 那个文件"
UNFORMATTED = "def demo(  ):\n  return(1+2)\n"
FORMATTED = "def demo():\n    return 1 + 2\n"


def condition(field, kind, value):
    return {"field": field, "match": {"type": kind, "value": value}}


def hook(name, event, kind, action, *, atoms=(), **options):
    rule = {
        "name": name,
        "event": event,
        "action": {"type": kind, **action},
        **options,
    }
    if atoms:
        rule["if"] = {"all_of": list(atoms)}
    return rule


def hook_config(path, rules):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"hooks": rules}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


class HookRun(TmuxRun):
    def __init__(self):
        super().__init__()
        self.http_requests = []
        requests = self.http_requests

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, body))
                data = (
                    {"decision": "block", "reason": "network policy"}
                    if self.path == "/check" and body["tool_name"] == "bash"
                    else {"ok": True}
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http_thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.http_thread.start()
        self.url = f"http://127.0.0.1:{self.http.server_port}"
        self.write_match = condition("tool_name", "exact", "write_file")
        self.block = hook(
            "block-write",
            "PreToolUse",
            "shell",
            {"command": f"echo '{BLOCKED}' >&2; exit 2"},
            atoms=[self.write_match],
        )
        self.reminder = hook(
            "zh-cn-default", "SessionStart", "prompt", {"text": REMINDER}
        )
        extract = shlex.join(
            [
                sys.executable,
                "-c",
                'import json, sys; print(json.load(sys.stdin)["tool_input"]["path"])',
            ]
        )
        self.format = hook(
            "ruff-after-write",
            "PostToolUse",
            "shell",
            {"command": f'sleep 1; ruff format -- "$({extract})"'},
            atoms=[
                self.write_match,
                condition("tool_input.path", "glob", "**/*.py"),
                condition("is_error", "exact", "False"),
            ],
            timeout="5s",
            **{"async": True},
        )
        self.delete = hook(
            "warn-delete",
            "UserPromptSubmit",
            "shell",
            {"command": "echo '用户消息含 delete 关键字' >&2; exit 2"},
            atoms=[condition("prompt", "regex", "(?i)delete")],
        )
        self.once = hook(
            "first-turn",
            "PreUserMessage",
            "shell",
            {"command": "echo first-turn-fired >&2"},
            only_once=True,
        )
        self.stop = hook("notify-stop", "Stop", "http", {"url": self.url + "/done"})

    def launch(self, session, rules, *, user_rules=(), live=False):
        work = ROOT / ".cache/tmux-e2e" / self.socket / session
        config = work / ".termcode"
        home = work / "home"
        home.mkdir(parents=True)
        hook_config(config / "hooks.yaml", rules)
        if user_rules:
            hook_config(home / ".termcode/hooks.yaml", user_rules)
        if live:
            settings = {
                "permissions": {
                    "allow": ["Write(=hello.py)", "Write(=demo.py)"],
                    "deny": [
                        "Bash",
                        "Read",
                        "Edit",
                        "Glob",
                        "Grep",
                        "LoadSkill",
                        "InstallSkill",
                    ],
                }
            }
            bootstrap = """
import asyncio
import sys
from urllib.parse import urlsplit

from termcode.cli import _run
from termcode.config import Config, load

provider = next(p for p in load(sys.argv[1]).providers if p.name == "zhipu")
endpoint = urlsplit(provider.base_url or "")
if endpoint.scheme != "https" or endpoint.hostname != "open.bigmodel.cn":
    raise SystemExit("智谱供应商地址与本次联调的目标不符")
asyncio.run(_run(Config([provider])))
"""
            command = shlex.join(
                [sys.executable, "-c", bootstrap, str(ROOT / ".termcode/config.yaml")]
            )
        else:
            settings = {"default_mode": "bypassPermissions"}
            command = shlex.join([sys.executable, "-m", "termcode"])
            (config / "config.yaml").write_text(
                yaml.safe_dump({"providers": [self.entry("openai", "local-hooks")]}),
                encoding="utf-8",
            )
        (config / "settings.yaml").write_text(
            yaml.safe_dump(settings), encoding="utf-8"
        )
        runner = work / "run.sh"
        runner.write_text(
            "export LANG=C.UTF-8 TERM=xterm-256color\n"
            f"export HOME={shlex.quote(str(home))}\n"
            f"export GIT_CEILING_DIRECTORIES={shlex.quote(str(work.parent))}\n"
            f"export PATH={shlex.quote(str(Path(sys.executable).parent))}:$PATH\n"
            "while [ ! -f start.release ]; do sleep 0.05; done\n"
            "stty -g > stty-before.txt\n"
            f"{command}\n"
            "status=$?\nstty -g > stty-after.txt\n"
            "printf '\\nTERMCODE_EXIT=%s\\n' \"$status\"\n",
            encoding="utf-8",
        )
        self.tmux(
            "new-session",
            "-d",
            "-s",
            session,
            "-x",
            "120",
            "-y",
            "40",
            "-c",
            str(work),
            shlex.join(["sh", str(runner)]),
        )
        self.tmux("set-option", "-t", session, "remain-on-exit", "on")
        self.tmux(
            "pipe-pane",
            "-o",
            "-t",
            session,
            "cat >> " + shlex.quote(str(work / "terminal.raw")),
        )
        (work / "start.release").touch()
        self.wait(lambda: "Send a message" in self.capture(session), "启动界面", 120)
        print(f"READY {session}", flush=True)
        return work

    def submit(self, session, text):
        self.send(session, text)
        self.key(session, "Enter")

    def run_turn(self, session, text, *, reply, calls=(), retry=()):
        tool_rounds = [list(batch) for batch in (calls, retry) if batch]
        self.server.responses.extend({"calls": batch} for batch in tool_rounds)
        self.server.responses.append({"text": reply})
        before = len(self.server.requests)
        self.submit(session, text)
        self.wait(lambda: reply in self.capture(session), reply)
        self.wait(lambda: self.turn_idle(session), "本轮结束")
        assert len(self.server.requests) == before + len(tool_rounds) + 1
        return [record["body"]["messages"] for record in self.server.requests[before:]]

    def turn_idle(self, session):
        pane = self.capture(session)
        return (
            "Send a message" in pane
            and "Imagining" not in pane
            and "Running…" not in pane
        )

    def write(self, path, content):
        return {
            "id": "write-1",
            "name": "write_file",
            "input": json.dumps({"path": path, "content": content}),
        }

    def complete_case(self, session, work, evidence, *, ctrl_c=False):
        self.capture(session, f"{session}-verified")
        self.finish(session, work, ctrl_c=ctrl_c)
        raw = self.terminal_output(work)
        assert "Traceback (most recent call last)" not in raw
        self.results["cases"][-1]["evidence"] = evidence
        print(f"PASS {session}", flush=True)

    @staticmethod
    def terminal_output(work):
        return (work / "terminal.raw").read_text(encoding="utf-8", errors="replace")

    def check_delete(self, session):
        before = len(self.server.requests)
        self.submit(session, DELETE)
        self.wait(lambda: "[hook warn-delete]" in self.capture(session), "提交拦截")
        pane = self.capture(session, f"{session}-blocked-input")
        assert DELETE in pane and "用户消息含 delete 关键字" in pane
        assert len(self.server.requests) == before
        self.key(session, "Home")
        self.key(session, "S-End")
        self.key(session, "BSpace")
        self.wait(lambda: "Send a message" in self.capture(session), "清空验收输入")

    def check_reminder(self, requests):
        assert requests[0][-1] == {"role": "user", "content": REMINDER}

    def run(self):
        session = "ch12-01-block"
        work = self.launch(session, [self.block])
        requests = self.run_turn(
            session,
            "创建 hello.txt，内容是 hi",
            reply="写入被策略拦截",
            calls=[self.write("hello.txt", "hi")],
        )
        assert requests[-1][-1]["role"] == "tool"
        assert f"[hook block-write] {BLOCKED}" in requests[-1][-1]["content"]
        assert f"[hook block-write] {BLOCKED}" in self.capture(session)
        assert not (work / "hello.txt").exists()
        self.complete_case(session, work, "工具结果回灌，文件不存在")

        session = "ch12-02-reminder"
        work = self.launch(session, [self.reminder])
        self.check_reminder(
            self.run_turn(session, "hi there", reply="你好，需要我帮你吗？")
        )
        requests = self.run_turn(session, "say hi again", reply="再次问好")
        assert all(REMINDER not in msg["content"] for msg in requests[0])
        self.complete_case(session, work, "首个请求含临时提醒，后续请求不含提醒")

        session = "ch12-03-format"
        allow = {**self.block, "action": {"type": "shell", "command": "exit 0"}}
        gated = {
            **self.format,
            "action": {
                "type": "shell",
                "command": "while [ ! -f format.release ]; do sleep 0.05; done; "
                + self.format["action"]["command"],
            },
        }
        work = self.launch(session, [allow, gated])
        self.run_turn(
            session,
            "写一个未格式化的 demo.py",
            reply="已写入文件",
            calls=[self.write("demo.py", UNFORMATTED)],
        )
        target = work / "demo.py"
        assert target.read_text() == UNFORMATTED
        (work / "format.release").touch()
        self.wait(lambda: target.read_text() == FORMATTED, "ruff 后台格式化")
        self.complete_case(session, work, "exit 0 放行，模型先续答，ruff 随后完成")

        session = "ch12-04-submit"
        work = self.launch(session, [self.delete])
        self.check_delete(session)
        archives = (work / ".termcode/sessions").glob("*/conversation.jsonl")
        assert all(archive.stat().st_size == 0 for archive in archives)
        self.complete_case(session, work, "输入保留，模型与历史未收到消息")

        session = "ch12-05-http"
        policy = hook(
            "network-policy", "PreToolUse", "http", {"url": self.url + "/check"}
        )
        work = self.launch(session, [self.stop, policy])
        requests = self.run_turn(
            session,
            "运行 git status",
            reply="命令被网络策略拦截",
            calls=[{"id": "git", "name": "bash", "input": '{"command":"git status"}'}],
        )
        assert "[hook network-policy] network policy" in requests[-1][-1]["content"]
        self.wait(lambda: len(self.http_requests) == 2, "收到策略和 Stop 两次 POST")
        assert [path for path, _ in self.http_requests] == ["/check", "/done"]
        payload = self.http_requests[-1][1]
        assert payload["event"] == "Stop" and payload["iter"] == 2
        self.complete_case(session, work, "HTTP 拦截 Bash，Stop POST 仅一次")

        session = "ch12-06-once"
        work = self.launch(session, [self.once])
        self.run_turn(session, "介绍一下你自己", reply="第一次回答")
        self.run_turn(session, "再说一句", reply="第二次回答")
        assert self.terminal_output(work).count("first-turn-fired") == 1
        self.submit(session, "/clear")
        self.wait(lambda: "已清空当前会话" in self.capture(session), "新会话")
        self.run_turn(session, "你好，新会话", reply="新会话回答")
        assert self.terminal_output(work).count("first-turn-fired") == 2
        self.complete_case(session, work, "同会话仅一次，clear 后重新触发")

        session = "ch12-07-invalid"
        invalid = [
            {**self.block, "name": "bad-async", "async": True},
            {**self.block, "name": "bad-event", "event": "UnknownEvent"},
            {**self.block, "name": "bad-if", "if": {"all_of": [], "any_of": []}},
        ]
        good = hook(
            "good-hook",
            "SessionStart",
            "subagent",
            {"agent_name": "foo", "prompt": "test"},
        )
        work = self.launch(session, [*invalid, good])
        stderr = self.terminal_output(work)
        assert (
            'hook "bad-async": async not allowed for blocking events, skipped' in stderr
        )
        assert 'hook "bad-event": unknown event "UnknownEvent", skipped' in stderr
        assert 'hook "bad-if": if must contain exactly one' in stderr
        assert "[hook subagent] not yet implemented, skipped: foo" in stderr
        self.submit(session, "/hooks")
        self.wait(lambda: "good-hook" in self.capture(session), "正常规则可查询")
        assert "bad-async  PreToolUse" not in self.capture(session)
        self.complete_case(session, work, "错误规则逐条跳过，子 Agent 占位日志")

        session = "ch12-08-list"
        second_start = {**self.reminder, "name": "other-start"}
        work = self.launch(
            session, [self.reminder, self.once], user_rules=[second_start]
        )
        self.submit(session, "/hooks")
        self.wait(lambda: "Loaded from:" in self.capture(session), "查询加载规则")
        pane = self.capture(session)
        assert (
            pane.index("zh-cn-default")
            < pane.index("other-start")
            < pane.index("first-turn")
        )
        assert "first-turn  PreUserMessage  shell  [once]" in pane
        paths = "".join(pane.split())
        assert str(work / ".termcode/hooks.yaml") in paths
        assert str(work / "home/.termcode/hooks.yaml") in paths
        self.complete_case(session, work, "按事件分组，显示两层来源与标志", ctrl_c=True)

        self.run_combined()

    def run_combined(self):
        session = "ch12-09-combined"
        selective = {
            **self.block,
            "if": {
                "all_of": [
                    self.write_match,
                    condition("tool_input.path", "exact", "hello.py"),
                ]
            },
        }
        work = self.launch(
            session, [selective, self.reminder, self.format, self.delete]
        )
        requests = self.run_turn(
            session,
            "创建 hello.py，然后用 ruff 格式化",
            reply="hello.py 被拦截，改用 demo.py",
            calls=[self.write("hello.py", UNFORMATTED)],
            retry=[self.write("demo.py", UNFORMATTED) | {"id": "write-2"}],
        )
        self.check_reminder(requests)
        assert f"[hook block-write] {BLOCKED}" in requests[1][-1]["content"]
        assert not (work / "hello.py").exists()
        assert requests[2][-1]["tool_call_id"] == "write-2"
        self.wait(
            lambda: (work / "demo.py").read_text() == FORMATTED, "组合场景后台格式化"
        )
        self.check_delete(session)
        self.submit(session, "/hooks")
        self.wait(lambda: "Loaded from:" in self.capture(session), "组合场景查询")
        pane = self.capture(session)
        assert all(
            rule["name"] in pane
            for rule in [self.block, self.reminder, self.format, self.delete]
        )
        self.complete_case(
            session, work, "首轮提醒、拦截、重试和后台格式化，随后拦截输入并查询"
        )

    def run_live(self):
        self.results["backend"] = "项目配置的智谱模型"
        session = "ch12-live"
        reminder = {
            **self.reminder,
            "action": {
                "type": "prompt",
                "text": "每次回复正文请用中文，以「钩子已启用」开头。",
            },
        }
        selective = {
            **self.block,
            "if": {
                "all_of": [
                    self.write_match,
                    condition("tool_input.path", "glob", "**/hello.py"),
                ]
            },
        }
        work = self.launch(
            session, [selective, reminder, self.format, self.delete], live=True
        )
        self.submit(session, "Say hello in one sentence.")
        self.wait(
            lambda: "钩子已启用" in self.capture(session) and self.turn_idle(session),
            "真实模型按提醒回复",
            100,
        )
        self.capture(session, f"{session}-model-reminder")
        self.submit(
            session,
            "Use write_file with the relative path hello.py and content x=1. "
            "If the tool is blocked, do not retry. Explain the result in one sentence.",
        )
        self.wait(
            lambda: f"[hook block-write] {BLOCKED}" in self.capture(session),
            "真实工具拦截",
            100,
        )
        self.wait(lambda: self.turn_idle(session), "真实模型收到拦截结果并续答", 100)
        assert not (work / "hello.py").exists()
        self.capture(session, f"{session}-model-block")
        self.submit(
            session,
            "请只调用一次 write_file，把未格式化的 Python 代码 x=1 原样写入 demo.py。"
            "请不要执行格式化或其他工具，写完简单告诉我。",
        )
        target = work / "demo.py"
        self.wait(
            lambda: target.exists() and target.read_text() == "x = 1\n",
            "真实模型写入后后台格式化",
            100,
        )
        self.wait(lambda: self.turn_idle(session), "真实模型本轮结束", 100)
        self.check_delete(session)
        self.submit(session, "/hooks")
        self.wait(lambda: "Loaded from:" in self.capture(session), "真实模型组合查询")
        self.complete_case(
            session, work, "真实工具调用、模型回复、后台格式化和提交拦截"
        )

    def close(self):
        self.http.shutdown()
        self.http.server_close()
        self.http_thread.join()
        super().close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="使用项目已配置的真实模型")
    args = parser.parse_args()
    run = HookRun()
    try:
        if args.live:
            run.run_live()
        else:
            run.run()
        result = EVIDENCE / (
            "ch12-live-results.json" if args.live else "ch12-results.json"
        )
        result.write_text(
            json.dumps(run.results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    finally:
        run.close()
