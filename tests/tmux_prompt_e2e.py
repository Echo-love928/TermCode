"""ch05 真 tmux 验收：默认本地协议服务，--live 调用已配置的真实模型。"""

import json
import shlex
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import date
from pathlib import Path

import tmux_e2e
from test_tool_protocols import call
from tmux_e2e import EVIDENCE, ROOT
from tmux_loop_e2e import LoopRun

from termcode.config import load
from termcode.prompt import plan_reminder


class PromptRun(LoopRun):
    def ask(self, session, prompt, marker, responses=None):
        if responses is not None:
            self.server.responses = responses
        self.send(session, prompt)
        self.key(session, "Enter")
        # 输入区会先回显请求中的 marker，必须观察忙碌状态后才等完成。
        self.wait(
            lambda: any(
                word in self.capture(session) for word in ("Imagining", "Running…")
            ),
            "请求已开始处理",
            seconds=30,
        )
        stable_since = None

        def finished():
            nonlocal stable_since
            pane = self.capture(session)
            idle = marker in pane and "Send a message" in pane
            idle = idle and "Imagining" not in pane and "Running…" not in pane
            if not idle:
                stable_since = None
                return False
            if stable_since is None:
                stable_since = time.monotonic()
            return time.monotonic() - stable_since >= 0.3

        self.wait(finished, marker, seconds=180)

    def check_prompt(self, protocol, session, work):
        sources = [work / f"source{i}.txt" for i in range(5)]
        for i, source in enumerate(sources):
            source.write_text(
                f"第 {i + 1} 段依据：系统提示分离稳定内容和环境。\n", encoding="utf-8"
            )
        target = work / "planned.txt"
        self.send(session, "/plan")
        self.key(session, "Enter")
        self.wait(lambda: "PLAN" in self.capture(session), "进入规划模式")
        previous = len(self.server.requests)
        prompt = (
            f"请按顺序逐一调用 read_file 读取 {', '.join(str(p) for p in sources)}，"
            "每次只读一个文件，"
            f"拿到结果后再读下一个。全部读完后提出创建 {target} 的计划，"
            "内容为五份依据的一句话摘要，此阶段不能写文件。"
            "计划完成时末尾写 E2E_PLAN_OK；之后用户 /do 批准执行，"
            "实际写入后最终回复末尾写 E2E_DO_OK。"
        )
        responses = (
            None
            if self.live
            else [
                *[{"calls": [call("read_file", path=str(p))]} for p in sources],
                {"text": "计划：创建一句话摘要文件，等待批准。E2E_PLAN_OK"},
            ]
        )
        self.ask(session, prompt, "E2E_PLAN_OK", responses)
        assert not target.exists()
        pane = self.capture(session, f"ch05-{self.live}-{protocol}-plan")
        assert "<system-reminder>" not in pane
        if not self.live:
            requests = [r["body"] for r in self.server.requests[previous:]]
            assert len(requests) == 6
            for i, body in enumerate(requests):
                reminder = (
                    body["messages"][-1]["content"][-1]["text"]
                    if protocol == "anthropic"
                    else body["messages"][-1]["content"]
                )
                assert reminder == plan_reminder(i % 4 == 0)
                definitions = body["tools"]
                assert [
                    d["name"] if protocol == "anthropic" else d["function"]["name"]
                    for d in definitions
                ] == ["read_file", "glob", "grep"]
            self.results[f"{protocol}_cadence"] = (
                "full, short, short, short, full, short"
            )
        self.ask(
            session,
            "/do",
            "E2E_DO_OK",
            None
            if self.live
            else [
                {
                    "calls": [
                        call(
                            "write_file",
                            path=str(target),
                            content="稳定提示与环境分离。\n",
                        )
                    ]
                },
                {"text": "已创建摘要。E2E_DO_OK"},
            ],
        )
        self.capture(session, f"ch05-{self.live}-{protocol}-do")
        assert target.exists() and target.read_text(encoding="utf-8").strip()
        self.ask(
            session,
            "不要调用工具，直接根据系统提供的环境信息回答当前目录、平台和今天日期。"
            "末尾写 E2E_ENV_OK。",
            "E2E_ENV_OK",
            None
            if self.live
            else [
                {
                    "text": f"当前目录 {work}，平台 linux，"
                    f"日期 {date.today()}。E2E_ENV_OK"
                }
            ],
        )
        env_pane = self.capture(session, f"ch05-{self.live}-{protocol}-environment")
        assert date.today().isoformat() in env_pane and "linux" in env_pane.lower()
        self.send(session, "/plan")
        self.key(session, "Enter")
        self.wait(lambda: "PLAN" in self.capture(session), "再次进入规划模式")
        previous = len(self.server.requests)
        if not self.live:
            self.server.gate.clear()
        self.send(session, f"请逐一详细读取 {sources} 并规划后续工作，暂不写文件。")
        self.key(session, "Enter")
        self.wait(
            lambda: (
                "Imagining" in self.capture(session)
                if self.live
                else len(self.server.requests) > previous
            ),
            "规划请求运行中",
        )
        self.key(session, "Escape")
        self.wait(
            lambda: "已取消" in self.capture(session) and self.idle(session),
            "显示取消结果并回到空闲",
        )
        self.capture(session, f"ch05-{self.live}-{protocol}-cancel")
        if not self.live:
            self.server.gate.set()
            time.sleep(0.5)
        self.ask(
            session,
            "停止之前的任务，不调用工具，只回复 E2E_RESUME_OK。",
            "E2E_RESUME_OK",
            None if self.live else [{"text": "E2E_RESUME_OK"}],
        )
        self.capture(session, f"ch05-{self.live}-{protocol}-resume")
        self.finish(session, work)
        final = self.capture(session, f"ch05-{self.live}-{protocol}-exit")
        # 退出后 CLI 打印完整静态记录，避免把滚动区外的工具误判为未执行。
        assert "● read_file(" in final and "● write_file(" in final
        assert "<system-reminder>" not in final
        self.results[protocol] = {
            "plan_read_only_and_do_write": True,
            "reminder_not_echoed": True,
            "environment_answer": True,
            "plan_cancel_resume": True,
            "terminal_restored": True,
        }

    def smoke(self, entry):
        protocol = entry["protocol"]
        session = f"cache-{protocol}"
        # smoke 使用与 TUI 相同的 Agent，只输出用量和工具状态。
        work = ROOT / ".cache/tmux-e2e" / self.socket / f"prompt-{protocol}"
        log = EVIDENCE / f"ch05-live-{protocol}-smoke.txt"
        command = shlex.join(
            [
                sys.executable,
                str(ROOT / "examples/smoke.py"),
                "--config",
                str(work / ".termcode/config.yaml"),
                "你好，不调用工具，只回复 CACHE_FIRST_OK。",
                "不调用工具，只回复 CACHE_SECOND_OK。",
            ]
        )
        runner = work / "smoke.sh"
        runner.write_text(
            f"{command} > {shlex.quote(str(log))} 2>&1\n"
            "printf 'SMOKE_EXIT=%s\\n' \"$?\"\nsleep 2\n",
            encoding="utf-8",
        )
        self.tmux(
            "new-session",
            "-d",
            "-s",
            session,
            "-c",
            str(work),
            shlex.join(["sh", str(runner)]),
        )
        self.tmux("set-option", "-t", session, "remain-on-exit", "on")
        self.wait(
            lambda: "SMOKE_EXIT=" in self.capture(session),
            "缓存 smoke 完成",
            seconds=180,
        )
        assert "SMOKE_EXIT=0" in self.capture(session)
        text = log.read_text(encoding="utf-8")
        assert all(secret not in text for secret in self.secrets)
        usage = [
            json.loads(line)["usage"]
            for line in text.splitlines()
            if line.startswith('{"usage":')
        ]
        assert len(usage) == 2
        self.results[f"{protocol}_live_cache_usage"] = usage
        print(json.dumps({"protocol": protocol, "usage": usage}), flush=True)

    def run(self):
        entries = (
            [asdict(p) for p in load(ROOT / ".termcode/config.yaml").providers]
            if self.live
            else []
        )
        self.secrets = [p["api_key"] for p in entries]
        self.server.delay = 0.01
        for protocol in ("openai", "anthropic"):
            entry = (
                next((p for p in entries if p["protocol"] == protocol), None)
                if self.live
                else self.entry(protocol, f"prompt-{protocol}")
            )
            if entry is None:
                self.results[protocol] = "缺少配置，未验收"
                continue
            session = f"prompt-{protocol}"
            work = self.start(session, [entry])
            self.check_prompt(protocol, session, work)
            if self.live:
                self.smoke(entry)
            # 在真正不受仓库 git 管理的临时目录内启动第二个 CLI。
            with tempfile.TemporaryDirectory(prefix="termcode-ch05-") as temporary:
                original = tmux_e2e.ROOT
                try:
                    tmux_e2e.ROOT = Path(temporary)
                    nongit = self.start(f"nongit-{protocol}", [entry])
                    self.ask(
                        f"nongit-{protocol}",
                        "不要调用工具，回答当前平台与日期，末尾写 E2E_NONGIT_OK。",
                        "E2E_NONGIT_OK",
                        None
                        if self.live
                        else [{"text": "linux 环境可以正常对话。E2E_NONGIT_OK"}],
                    )
                    self.capture(
                        f"nongit-{protocol}", f"ch05-{self.live}-{protocol}-nongit"
                    )
                    self.finish(f"nongit-{protocol}", nongit)
                    self.results[protocol]["nongit_conversation"] = True
                finally:
                    tmux_e2e.ROOT = original


if __name__ == "__main__":
    run = PromptRun(live="--live" in sys.argv)
    try:
        run.run()
    finally:
        output = EVIDENCE / (
            "ch05-live-results.json" if run.live else "ch05-tmux-results.json"
        )
        output.write_text(
            json.dumps(run.results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(run.results, ensure_ascii=False, indent=2), flush=True)
        run.close()
