"""ch06：真实 tmux 界面的确定性协议验收，以及已配置真实模型验收。"""

import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import asdict

from test_tool_protocols import call
from tmux_e2e import EVIDENCE, ROOT, TmuxRun

from termcode.config import load


class PermissionRun(TmuxRun):
    def __init__(self, live=False):
        super().__init__()
        self.live = live
        self.secrets = []
        self.results["backend"] = (
            "configured live models" if live else "local SSE fixture"
        )
        self.server.delay = 0.02
        guard = ROOT / ".cache/ch06-command-guard"
        guard.mkdir(exist_ok=True)
        stub = guard / "rm"
        stub.write_text("#!/bin/sh\nprintf 'UNEXPECTED_RM_EXECUTION\\n' >&2\nexit 77\n")
        stub.chmod(0o755)
        # 独立 tmux 服务中的 rm 为无破坏替身；若黑名单失效，测试仍不会删文件。
        self.tmux("new-session", "-d", "-s", "guard", "sleep 3600")
        self.tmux(
            "set-environment",
            "-g",
            "PATH",
            str(guard) + os.pathsep + os.environ["PATH"],
        )

    def capture(self, session, name=None):
        text = super().capture(session, name)
        assert all(secret not in text for secret in self.secrets if secret)
        assert "UNEXPECTED_RM_EXECUTION" not in text
        return text

    def idle(self, session):
        pane = self.capture(session)
        return (
            all(word not in pane for word in ("Imagining", "Running…", "是否继续?"))
            and "Send a message" in pane
        )

    def begin(self, session, prompt, responses=None):
        if responses is not None:
            self.server.responses = responses
        self.send(session, prompt)
        self.key(session, "Enter")

    def approved(self, session, keys, name):
        self.wait(
            lambda: "是否继续?" in self.capture(session), "待批准菜单", seconds=120
        )
        pane = self.capture(session, name + "-approval")
        assert "> 1. 允许本次" in pane and "永久允许" in pane and "拒绝本次" in pane
        for key in keys:
            self.key(session, key)
            time.sleep(0.12)

    def completed(self, session, marker, name):
        self.wait(
            lambda: marker in self.capture(session) and self.idle(session),
            marker,
            seconds=180,
        )
        self.capture(session, name)

    def scripted(self, protocol, session, work):
        def write(path, content="ok"):
            return {"calls": [call("write_file", path=path, content=content)]}

        self.begin(
            session,
            "请创建 one.txt，内容为 ok。",
            [write("one.txt"), {"text": "SCENE1_DONE"}],
        )
        self.approved(session, ["1"], f"ch06-{protocol}-scene1")
        self.completed(session, "SCENE1_DONE", f"ch06-{protocol}-scene1")
        assert (work / "one.txt").read_text() == "ok"
        assert not (work / ".termcode/settings.local.yaml").exists()

        self.begin(
            session,
            "请写项目外文件，若被拒请改为项目内 safe.txt。",
            [write("../outside.txt"), write("safe.txt"), {"text": "SCENE2_DONE"}],
        )
        self.approved(session, ["1"], f"ch06-{protocol}-scene2")
        self.completed(session, "SCENE2_DONE", f"ch06-{protocol}-scene2")
        assert (work / "safe.txt").exists() and not (
            work.parent / "outside.txt"
        ).exists()
        assert "路径在项目目录之外" in self.capture(session)

        self.begin(
            session,
            "请创建 reject.txt。",
            [write("reject.txt"), {"text": "SCENE3_DONE"}],
        )
        self.approved(session, ["Up", "Enter"], f"ch06-{protocol}-scene3")
        self.completed(session, "SCENE3_DONE", f"ch06-{protocol}-scene3")
        assert not (work / "reject.txt").exists()

        self.begin(
            session,
            "请创建 persistent.txt。",
            [write("persistent.txt"), {"text": "SCENE4_DONE"}],
        )
        self.approved(session, ["Down", "Enter"], f"ch06-{protocol}-scene4")
        self.completed(session, "SCENE4_DONE", f"ch06-{protocol}-scene4")
        local = work / ".termcode/settings.local.yaml"
        assert "Write(persistent.txt)" in local.read_text()
        self.finish(session, work)
        self.tmux(
            "respawn-pane",
            "-k",
            "-t",
            session,
            shlex.join(["sh", str(work / "run.sh")]),
        )
        self.wait(
            lambda: (
                "↑0 ↓0 tok" in self.tmux("capture-pane", "-p", "-t", session)
                and self.idle(session)
            ),
            "重启后空闲",
            seconds=120,
        )
        self.begin(
            session,
            "再次写 persistent.txt。",
            [write("persistent.txt", "reloaded"), {"text": "RELOAD_DONE"}],
        )
        self.completed(session, "RELOAD_DONE", f"ch06-{protocol}-scene4-reload")
        assert (work / "persistent.txt").read_text() == "reloaded"

        self.key(session, "BTab")
        self.wait(lambda: "ACCEPT EDITS" in self.capture(session), "acceptEdits 模式")
        self.begin(
            session,
            "请写 auto.txt，然后把 ok 改成 edited。",
            [
                write("auto.txt"),
                {
                    "calls": [
                        call(
                            "edit_file",
                            path="auto.txt",
                            old_string="ok",
                            new_string="edited",
                        )
                    ]
                },
                {"text": "AUTO_DONE"},
            ],
        )
        self.completed(session, "AUTO_DONE", f"ch06-{protocol}-scene5-write")
        assert (work / "auto.txt").read_text() == "edited"
        self.begin(
            session,
            "请执行 printf safe。",
            [{"calls": [call("bash", command="printf safe")]}, {"text": "EXEC_DONE"}],
        )
        self.approved(session, ["1"], f"ch06-{protocol}-scene5-exec")
        self.completed(session, "EXEC_DONE", f"ch06-{protocol}-scene5-exec")

        for _ in range(2):
            self.key(session, "BTab")
            time.sleep(0.15)
        self.wait(lambda: "BYPASS" in self.capture(session), "bypass 模式")
        self.begin(
            session,
            "请执行普通命令，然后检查危险命令是否被拒。",
            [
                {"calls": [call("bash", command="printf safe")]},
                {"calls": [call("bash", command="rm -rf /")]},
                {"text": "BLACKLIST_DONE"},
            ],
        )
        self.completed(session, "BLACKLIST_DONE", f"ch06-{protocol}-scene6")
        assert "命中危险命令黑名单" in self.capture(session)

        self.begin(
            session,
            "请读取 /etc/passwd。",
            [
                {"calls": [call("read_file", path="/etc/passwd")]},
                {"text": "SANDBOX_DONE"},
            ],
        )
        self.completed(session, "SANDBOX_DONE", f"ch06-{protocol}-scene7")
        assert "路径在项目目录之外" in self.capture(session)

        self.send(session, "/plan")
        self.key(session, "Enter")
        self.wait(lambda: "PLAN" in self.capture(session), "计划模式")
        before = len(self.server.requests)
        self.begin(
            session,
            "请读 one.txt 后规划创建 planned.txt。",
            [{"calls": [call("read_file", path="one.txt")]}, {"text": "PLAN_DONE"}],
        )
        self.completed(session, "PLAN_DONE", f"ch06-{protocol}-scene8-plan")
        tools = self.server.requests[before]["body"]["tools"]
        names = [
            t["function"]["name"] if protocol == "openai" else t["name"] for t in tools
        ]
        assert names == ["read_file", "glob", "grep"]
        self.begin(session, "/do", [write("planned.txt"), {"text": "DO_DONE"}])
        self.approved(session, ["1"], f"ch06-{protocol}-scene8-do")
        self.completed(session, "DO_DONE", f"ch06-{protocol}-scene8-do")
        assert (work / "planned.txt").exists()

        for key in ("Escape", "C-c"):
            self.begin(session, "请创建 cancelled.txt。", [write("cancelled.txt")])
            self.approved(session, [key], f"ch06-{protocol}-scene9-{key}")
            self.wait(lambda: self.idle(session), "取消返回空闲")
            assert not (work / "cancelled.txt").exists()
            self.begin(session, "取消后请继续回复。", [{"text": f"RESUME_{key}_DONE"}])
            self.completed(
                session, f"RESUME_{key}_DONE", f"ch06-{protocol}-scene9-{key}-resume"
            )
        self.finish(session, work)
        self.results[protocol] = {
            "scenes_1_to_9": "passed",
            "cancel_keys": ["Esc", "Ctrl+C"],
            "permanent_reload": True,
        }

    def real_model(self, protocol, session, work):
        self.begin(
            session,
            "请使用 write_file 工具在当前项目根目录创建 live.txt，"
            "内容严格为 live permission ok。完成后简短回复。不要调用 Bash。",
        )
        self.approved(session, ["1"], f"ch06-live-{protocol}-write")
        self.wait(
            lambda: self.idle(session) and (work / "live.txt").exists(),
            "真实模型写入并完成",
            seconds=180,
        )
        self.capture(session, f"ch06-live-{protocol}-write-result")
        assert (work / "live.txt").read_text().strip() == "live permission ok"
        self.begin(
            session,
            "请使用 write_file 工具创建 cancelled-live.txt，"
            "内容为 cancel。不要调用 Bash。",
        )
        self.approved(session, ["Escape"], f"ch06-live-{protocol}-cancel")
        self.wait(lambda: self.idle(session), "真实模型审批取消")
        self.begin(session, "上一轮已取消，请不要调用工具，只回复继续成功。")
        self.completed(session, "继续成功", f"ch06-live-{protocol}-resume")
        assert not (work / "cancelled-live.txt").exists()
        self.finish(session, work)
        self.results[protocol] = {
            "live_write_with_approval": "passed",
            "live_cancel_resume": "passed",
        }

    def run(self):
        entries = (
            [asdict(c) for c in load(ROOT / ".termcode/config.yaml").providers]
            if self.live
            else []
        )
        self.secrets = [c["api_key"] for c in entries]
        for protocol in ("openai", "anthropic"):
            entry = (
                next((c for c in entries if c["protocol"] == protocol), None)
                if self.live
                else self.entry(protocol, "permission-test")
            )
            if entry is None:
                self.results[protocol] = "缺少配置，未验收"
                continue
            session = f"permission-{'live' if self.live else 'fixture'}-{protocol}"
            work = self.start(session, [entry])
            (self.real_model if self.live else self.scripted)(protocol, session, work)
        if not self.live:
            self.server.responses = [
                {
                    "calls": [
                        call(
                            "write_file", path=".cache/smoke-ch06.txt", content="smoke"
                        )
                    ]
                },
                {"text": "SMOKE_DONE"},
            ]
            work = ROOT / ".cache/tmux-e2e" / self.socket / "permission-fixture-openai"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "smoke",
                    "--config",
                    str(work / ".termcode/config.yaml"),
                    "写测试文件",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0 and "SMOKE_DONE" in result.stdout
            assert (ROOT / ".cache/smoke-ch06.txt").read_text() == "smoke"
            self.results["python_m_smoke"] = "passed"


if __name__ == "__main__":
    run = PermissionRun(live="--live" in sys.argv)
    try:
        run.run()
    finally:
        path = EVIDENCE / (
            "ch06-live-results.json" if run.live else "ch06-tmux-results.json"
        )
        path.write_text(
            json.dumps(run.results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(run.results, ensure_ascii=False), flush=True)
        run.close()
