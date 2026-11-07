"""在真实 tmux 中验证 ch11，默认使用本地模型和下载测试服务。"""

import argparse
import base64
import json
import re
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx2
import yaml
from tmux_e2e import EVIDENCE, ROOT, TmuxRun

from termcode.config import load
from termcode.session import load_session
from termcode.skills import install

SESSION = "ch11-skills"
INSTALL_URL = "https://skills.sh/vercel-labs/skills/find-skills"


def mock_github() -> None:
    raw = b"---\nname: find-skills\ndescription: Find reusable skills\n---\nFixture SOP"

    def respond(req):
        assert req.url.host == "api.github.com"
        path = req.url.path
        if path.endswith("/contents/skills/find-skills"):
            return httpx2.Response(
                200, json=[{"type": "file", "name": "SKILL.md", "size": len(raw)}]
            )
        if path.endswith("/contents/skills/find-skills/SKILL.md"):
            return httpx2.Response(
                200,
                json={
                    "type": "file",
                    "encoding": "base64",
                    "content": base64.b64encode(raw).decode(),
                },
            )
        return httpx2.Response(404)

    def client(**kwargs):
        return httpx2.AsyncClient(transport=httpx2.MockTransport(respond), **kwargs)

    install.httpx2 = SimpleNamespace(
        AsyncClient=client, HTTPStatusError=httpx2.HTTPStatusError
    )


def skill_file(path: Path, name: str, body: str, extra: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: 验收用{name}流程\n{extra}---\n{body}\n",
        encoding="utf-8",
    )


def call(tool, **params):
    return {"calls": [{"id": tool + "-id", "name": tool, "input": json.dumps(params)}]}


class SkillRun(TmuxRun):
    def __init__(self, *, live=False, github=False):
        super().__init__()
        self.live = live
        self.github = github
        self.prefix = "ch11-live" if live else "ch11-github" if github else "ch11"
        self.results["backend"] = "配置中的模型" if live else "本地 HTTP/SSE"
        self.results["github"] = "GitHub Contents API" if github else "本地模拟"

    def command(self, text):
        self.send(SESSION, text)
        self.key(SESSION, "Enter")

    def evidence(self, label, *markers):
        self.wait(
            lambda: (
                all(marker in self.capture(SESSION) for marker in markers)
                and "Send a message" in self.capture(SESSION)
                and "Imagining" not in self.capture(SESSION)
            ),
            label,
            seconds=120 if self.live or label == "start" else 45,
        )
        pane = self.capture(SESSION, f"{self.prefix}-{label}")
        self.results["cases"].append(label)
        print(f"PASS {label}", flush=True)
        return pane

    def prepare(self):
        work = ROOT / ".cache/tmux-e2e" / self.socket / SESSION
        home = work / "home"
        self.test_skill = work / ".termcode/skills/test-skill/SKILL.md"
        self.fork_skill = work / ".termcode/skills/review.md"
        skill_file(self.test_skill, "test-skill", "Echo hello\n核对码 CH11_INLINE_531")
        skill_file(
            self.fork_skill,
            "review",
            "请读取 sample.txt 并报告其核对码。\n任务 $ARGUMENTS",
            "mode: fork\ncontext: none\nmodel: fork-test-model\n",
        )
        skill_file(home / ".termcode/skills/shadow.md", "test-skill", "USER_SHADOW")
        (self.test_skill.parent.parent / "bad.md").write_text(
            "broken", encoding="utf-8"
        )
        (work / "sample.txt").write_text("CH11_READ_246", encoding="utf-8")
        source = ROOT / ".termcode/config.yaml"
        launch = "import runpy, sys; sys.__stderr__ = sys.stdout; "
        if self.live:
            self.secrets = [p.api_key for p in load(source).providers]
            launch += (
                "import termcode.config as config; original_load = config.load; "
                "config.load = lambda path: "
                f"config.Config(original_load({str(source)!r}).providers[:1]); "
            )
            (work / ".termcode/settings.yaml").write_text(
                yaml.safe_dump(
                    {"permissions": {"deny": ["Bash", "Write", "Edit", "InstallSkill"]}}
                ),
                encoding="utf-8",
            )
        else:
            self.secrets = []
            (work / ".termcode/config.yaml").write_text(
                yaml.safe_dump({"providers": [self.entry("openai", "ch11-local")]}),
                encoding="utf-8",
            )
        if not (self.github or self.live):
            launch += "from tmux_skills_e2e import mock_github; mock_github(); "
        launch += "runpy.run_module('termcode', run_name='__main__')"
        runner = work / "run.sh"
        python_path = shlex.quote(f"{ROOT / 'src'}:{ROOT / 'tests'}")
        runner.write_text(
            "export LANG=C.UTF-8 TERM=xterm-256color\n"
            f"export HOME={shlex.quote(str(home))}\n"
            f"export PYTHONPATH={python_path}\n"
            "stty -g > stty-before.txt\n"
            f"{shlex.join([sys.executable, '-c', launch])} 2> termcode.err\n"
            "status=$?\n"
            "stty -g > stty-after.txt\n"
            'printf "\nTERMCODE_EXIT=%s\n" "$status"\n',
            encoding="utf-8",
        )
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
        self.tmux("set-option", "-t", SESSION, "remain-on-exit", "on")
        self.evidence("start", "DEFAULT")
        warning = (work / "termcode.err").read_text(encoding="utf-8")
        assert "Skipping project skill 'bad'" in warning, warning
        self.results["cases"].append("bad-skill-warning")
        return work, home

    def request(self, index):
        return self.server.requests[index]["body"]

    def system(self, index):
        return self.request(index)["messages"][0]["content"]

    def scripted(self, work, home):
        self.command("/help")
        self.evidence("help", "/test-skill", "[skill]", "/skill", "管理已加载的 Skill")
        self.command("/skill list")
        self.evidence("list", "[project]", "验收用test-skill流程")
        self.command("/skill info test-skill")
        self.evidence("info", "path:", "is_directory: True", "mode: inline")
        assert not self.server.requests

        self.server.responses = [{"text": "hello CH11_INLINE_531"}]
        self.command("/test-skill")
        self.evidence("inline", "hello CH11_INLINE_531")
        assert "Echo hello" in self.system(0) and "USER_SHADOW" not in self.system(0)
        skill_file(
            self.test_skill, "test-skill", "热更新 CH11_UPDATED_194 参数 $ARGUMENTS"
        )
        self.server.responses = [{"text": "已读取热更新 CH11_UPDATED_194"}]
        self.command("/test-skill 指定目标")
        self.evidence("hot-reload", "已读取热更新 CH11_UPDATED_194")
        assert "CH11_UPDATED_194 参数 指定目标" in self.system(1)
        assert "Echo hello" not in self.system(1)

        self.server.responses = [
            call("read_file", path="sample.txt"),
            {"text": "fork 核对码 CH11_READ_246"},
        ]
        self.command("/review 独立核对")
        self.evidence("fork", "read_file(", "fork 核对码 CH11_READ_246")
        child = self.request(2)
        assert child["model"] == "fork-test-model"
        assert (
            len(child["messages"]) == 2
            and "任务 独立核对" in child["messages"][1]["content"]
        )
        assert "Active Skills" not in self.system(2)
        assert any(
            m["role"] == "tool" and "CH11_READ_246" in m["content"]
            for m in self.request(3)["messages"]
        )
        sessions = work / ".termcode/sessions"
        original = next(sessions.iterdir())
        history = load_session(str(original))
        assert (
            history[-1].role == "assistant" and "CH11_READ_246" in history[-1].content
        )
        assert len(history) == 5 and not any(m.role == "tool" for m in history)
        assert not list(sessions.glob(".fork-*"))

        self.command("/clear")
        self.evidence("clear-before-intent", "已清空当前会话")
        self.server.responses = [
            call("LoadSkill", name="test-skill"),
            call("read_file", path="sample.txt"),
            {"text": "自然语言触发完成 CH11_READ_246"},
        ]
        self.command("请按测试流程读取 sample.txt，并返回核对码。")
        pane = self.evidence(
            "intent", "LoadSkill(", "activated", "自然语言触发完成 CH11_READ_246"
        )
        assert "允许本次" not in pane
        assert "- test-skill: 验收用test-skill流程" in self.system(4)
        assert "Active Skills" not in self.system(
            4
        ) and "CH11_UPDATED_194" not in self.system(4)
        assert "CH11_UPDATED_194" in self.system(
            5
        ) and "CH11_UPDATED_194" in self.system(6)
        assert "SOP pinned" in self.request(5)["messages"][-1]["content"]
        assert "CH11_UPDATED_194" not in self.request(5)["messages"][-1]["content"]
        assert "CH11_READ_246" in self.request(6)["messages"][-1]["content"]

        self.command("/clear")
        self.evidence("clear-after-intent", "已清空当前会话")
        self.server.responses = [{"text": "清空后对话完成"}]
        self.command("请简单回答你好。")
        self.evidence("clear-confirmed", "清空后对话完成")
        assert "Active Skills" not in self.system(7)

        self.fork_skill.unlink()
        skill_file(self.fork_skill.with_name("fresh.md"), "fresh", "fresh SOP")
        self.command("/skill reload")
        self.evidence("reload", "已重新加载 Skill")
        self.command("/help")
        self.evidence("reload-help", "/fresh", "审查当前上下文中的代码")
        self.server.responses = [
            call("InstallSkill", url=INSTALL_URL),
            {"text": "安装操作完成 CH11_INSTALL_478"},
        ]
        self.command(f"请安装这个 Skill：{INSTALL_URL}")
        self.wait(lambda: "允许本次" in self.capture(SESSION), "安装权限确认")
        self.capture(SESSION, f"{self.prefix}-install-approval")
        self.key(SESSION, "1")
        self.evidence("install", "已安装 Skill", "安装操作完成 CH11_INSTALL_478")
        assert (home / ".termcode/skills/find-skills/SKILL.md").is_file()
        assert "- find-skills:" in self.system(9)
        self.command("/help")
        self.wait(
            lambda: re.search(r"(?m)^\s*/find-skills\s{2,}\S", self.capture(SESSION)),
            "已安装 Skill 出现在帮助",
        )
        self.evidence("install-help", "/find-skills")

    def live_turn(self):
        skill_file(
            self.test_skill,
            "test-skill",
            "用户要求执行验收测试流程时，仅回复核对码 CH11_LIVE_802。",
        )
        self.command("请按已提供的验收测试流程执行，返回流程里指定的核对码。")
        self.evidence("intent", "LoadSkill(", "activated", "CH11_LIVE_802", "完成 ·")

    def run(self):
        work, home = self.prepare()
        if self.live:
            self.live_turn()
        else:
            self.scripted(work, home)
        self.finish(SESSION, work)
        assert all(secret not in self.capture(SESSION) for secret in self.secrets)
        self.results["requests"] = len(self.server.requests)
        (EVIDENCE / f"{self.prefix}-results.json").write_text(
            json.dumps(self.results, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--live", action="store_true")
    group.add_argument("--github", action="store_true")
    opts = parser.parse_args()
    run = SkillRun(live=opts.live, github=opts.github)
    try:
        run.run()
        print(json.dumps(run.results, ensure_ascii=False, indent=2))
    finally:
        run.close()
