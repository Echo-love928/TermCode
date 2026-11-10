"""通过真实 tmux 和 TermCode CLI 验收 ch15。--live 只跑真实模型生命周期。"""

import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import yaml
from tmux_e2e import ROOT, TmuxRun
from tmux_subagent_e2e import role, tool_call

from termcode.session import load_session


class TeamRun(TmuxRun):
    def __init__(self, live):
        super().__init__()
        self.live = live
        self.server.response_for = self.response_for
        self.evidence = ROOT / ".cache/tmux-e2e" / self.socket
        self.workspace = TemporaryDirectory(prefix=f"{self.socket}-", dir="/tmp")
        self.results = {
            "provider": "智谱 glm-5.3-flash" if live else "local SSE",
            "cases": {},
        }
        self.temporary = []

    def data(self, path):
        return json.loads(path.read_text()) if path.is_file() else None

    def command(self, *args, cwd=None):
        return subprocess.check_output(args, cwd=cwd, text=True, stderr=subprocess.PIPE)

    def file(self, label):
        path = Path("/tmp") / f"{self.socket}-{label}.txt"
        self.temporary.append(path)
        return path

    def launch(self, case, *, inprocess=False, coordinator=False):
        self.case = case
        self.work = Path(self.workspace.name) / case
        self.capture_dir = self.evidence / case
        self.capture_dir.mkdir(parents=True)
        self.home = self.work / "home"
        self.teams = self.home / ".termcode/teams"
        config = self.work / ".termcode"
        (config / "agents").mkdir(parents=True)
        self.home.mkdir()
        (config / "agents/planner.md").write_text(
            role("planner", "permission_mode: plan\n", "先制定计划，等待 Lead 审批。"),
            encoding="utf-8",
        )
        (self.work / ".gitignore").write_text(
            ".termcode/\nhome/\nrun.sh\nno-tmux-bin/\n"
        )
        (self.work / "README.md").write_text("# Agent Teams 验收仓库\n")
        self.command("git", "init", "-q", cwd=self.work)
        self.command("git", "add", ".gitignore", "README.md", cwd=self.work)
        self.command(
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "initial",
            cwd=self.work,
        )
        (config / "settings.yaml").write_text(
            yaml.safe_dump({"default_mode": "bypassPermissions"}),
            encoding="utf-8",
        )
        path = config / "config.yaml"
        provider_index = 0
        providers = [self.entry("openai", "local-team")]
        if self.live:
            project_config = ROOT / ".termcode/config.yaml"
            providers = yaml.safe_load(project_config.read_text())["providers"]
            provider_index = next(
                i for i, p in enumerate(providers) if p["name"] == "zhipu"
            )
            url = urlsplit(providers[provider_index]["base_url"])
            assert url.scheme == "https" and url.hostname == "open.bigmodel.cn"
            path.symlink_to(project_config)
        else:
            path.write_text(
                yaml.safe_dump(
                    {
                        "providers": providers,
                        "features": {"coordinator_mode": coordinator},
                    }
                ),
                encoding="utf-8",
            )
        env = {
            "LANG": "C.UTF-8",
            "TERM": "xterm-256color",
            "HOME": str(self.home),
            "PYTHONPATH": str(ROOT / "src"),
            "TERMCODE_COORDINATOR_MODE": "1" if coordinator else "0",
        }
        lines = [f"export {key}={shlex.quote(value)}" for key, value in env.items()]
        if inprocess:
            bin_ = self.work / "no-tmux-bin"
            bin_.mkdir()
            for binary in ("git", "bash", "sh"):
                (bin_ / binary).symlink_to(shutil.which(binary))
            lines.extend(
                (
                    "unset TMUX TMUX_PANE TERM_PROGRAM",
                    f"export PATH={shlex.quote(str(bin_))}",
                )
            )
        lines.extend(
            (
                shlex.join([sys.executable, "-m", "termcode"]),
                "printf '\\nTERMCODE_EXIT=%s\\n' \"$?\"",
            )
        )
        runner = self.work / "run.sh"
        runner.write_text("\n".join(lines) + "\n")
        self.tmux(
            "new-session",
            "-d",
            "-s",
            case,
            "-x",
            "196",
            "-y",
            "48",
            "-c",
            str(self.work),
            shlex.join(["sh", str(runner)]),
        )
        self.tmux("set-option", "-t", case, "remain-on-exit", "on")
        self.lead = self.tmux("display-message", "-p", "-t", case, "#{pane_id}").strip()
        if len(providers) > 1:
            self.until(lambda: "选择供应商" in self.capture(self.lead), "供应商选择")
            self.key(self.lead, "Home")
            for _ in range(provider_index):
                self.key(self.lead, "Down")
            self.key(self.lead, "Enter")
        self.until(lambda: "Send a message" in self.capture(self.lead), "启动 Lead")
        self.archive = next((config / "sessions").glob("*/conversation.jsonl")).parent
        print(f"READY {case}", flush=True)

    def until(self, predicate, description, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if result := predicate():
                return result
            time.sleep(0.12)
        (self.capture_dir / "failure-lead.txt").write_text(
            self.capture(self.lead), encoding="utf-8"
        )
        for pane in self.panes():
            if pane != self.lead:
                (self.capture_dir / f"failure-{pane}.txt").write_text(
                    self.capture(pane), encoding="utf-8"
                )
        raise AssertionError(f"{self.case} 等待超时：{description}")

    def panes(self):
        return self.tmux("list-panes", "-t", self.case, "-F", "#{pane_id}").splitlines()

    def submit(self, text):
        self.send(self.lead, text)
        time.sleep(0.18)
        self.key(self.lead, "Enter")

    def replied(self, text):
        history = load_session(str(self.archive))
        return bool(
            history
            and history[-1].role == "assistant"
            and not history[-1].tool_calls
            and any(
                m.role == "user" and m.content.startswith(text) for m in history[:-1]
            )
        )

    def wait_reply(self, text):
        self.until(lambda: self.replied(text), f"回复 {text}")

    def create(self, team):
        prompt = f"请调用 TeamCreate 创建一个名为 {team} 的团队，然后简短回复。"
        self.submit(prompt)
        self.wait_reply(prompt)
        return self.until(
            lambda: self.data(self.teams / team / "config.json"), "团队配置落盘"
        )

    def member(self, team, name, *, idle=False):
        config = self.data(self.teams / team / "config.json")
        return (
            next(
                (
                    m
                    for m in config["members"]
                    if m["name"] == name and (not idle or m["is_active"] is False)
                ),
                None,
            )
            if config
            else None
        )

    def mail(self, team, agent_id="lead"):
        return self.data(self.teams / team / "mailbox" / f"{agent_id}.json") or []

    def spawn(self, team, name, task, role_name="general-purpose"):
        prompt = (
            f"请用 Agent 的 team_name={team}、name={name}、subagent_type={role_name}"
            f" 派队员在独立 Worktree 执行：{task}。派出后立刻结束本轮，等队员回报。"
        )
        self.submit(prompt)
        self.wait_reply(prompt)
        return self.until(lambda: self.member(team, name), f"{name} 加入团队")

    def delete(self, team, member):
        self.submit(f"/team delete {team} --force")
        self.until(lambda: not (self.teams / team).exists(), "删除团队")
        self.until(lambda: len(self.panes()) == 1, "清理窗格")
        assert not Path(member["worktree_path"]).exists()
        assert not Path(member["session_dir"]).exists()

    def record(self, **details):
        self.results["cases"][self.case] = details
        (self.evidence / "results.json").write_text(
            json.dumps(self.results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.capture_dir / "lead.txt").write_text(
            self.capture(self.lead), encoding="utf-8"
        )
        print(f"PASS {self.case}", flush=True)

    def lifecycle(self, inprocess=False):
        case, team, name = (
            ("in-process", "inproc", "bob") if inprocess else ("tmux", "demo", "alice")
        )
        self.launch(case, inprocess=inprocess)
        expected_backend = "in-process" if inprocess else "tmux"
        assert self.create(team)["backend"] == expected_backend
        self.submit("/team list")
        self.until(lambda: "1 成员" in self.capture(self.lead), "/team list")
        path, pwd = self.file(name), self.file(f"{name}-pwd")
        first, second = ("step1", "step2") if inprocess else ("hello", "world")
        initial = (
            f"用 bash 跑 echo {first} > {path} && pwd > {pwd}，"
            "用 SendMessage 给 Lead 汇报"
        )
        self.worker = {
            "name": name,
            "path": path,
            "pwd": pwd,
            "first": first,
            "second": second,
        }
        member = self.spawn(team, name, initial)
        assert member["backend_type"] == expected_backend
        assert Path(member["worktree_path"]).name == f"team-{team}+{name}"
        assert Path(member["worktree_path"]).is_dir()
        if inprocess:
            assert self.panes() == [self.lead]
        else:
            pane = member["pane_id"]
            assert pane and pane in self.panes()
            self.until(lambda: "[team-member]" in self.capture(pane), "窗格自治日志")
            pid = self.tmux("display-message", "-p", "-t", pane, "#{pane_pid}").strip()
            cmdline = self.command("ps", "-ww", "-o", "args=", "-p", pid)
            assert f"--agent-id {member['agent_id']}" in cmdline
            assert str(path) not in cmdline
            initial_mail = self.mail(team, member["agent_id"])[0]
            assert initial_mail["from"] == "lead" and str(path) in initial_mail["text"]
        self.until(
            lambda: path.is_file() and path.read_text().splitlines() == [first],
            "第一次写入",
        )
        self.until(
            lambda: (
                pwd.is_file() and pwd.read_text().strip() == member["worktree_path"]
            ),
            "Worktree cwd",
        )
        self.until(lambda: self.member(team, name, idle=True), "首次 idle")
        self.until(
            lambda: any(
                m["text"].startswith("[idle]") and m["read"] for m in self.mail(team)
            ),
            "Lead 已读 idle",
        )
        self.until(
            lambda: (
                "[team-update]" in self.capture(self.lead)
                and self.replied("[team-update]")
            ),
            "Lead 自动回复",
        )
        self.submit(f"/team info {team}")
        self.until(lambda: "is_active=False" in self.capture(self.lead), "/team info")
        prompt = (
            f"用 SendMessage 给 {name} 发消息，让她用 bash 跑 "
            f"echo {second} >> {path}，完成后汇报。"
        )
        self.submit(prompt)
        self.wait_reply(prompt)
        self.until(
            lambda: path.read_text().splitlines() == [first, second], "同一队员续派"
        )
        self.until(lambda: self.member(team, name, idle=True), "续派后 idle")
        history = load_session(member["session_dir"])
        assert sum(c.name == "bash" for m in history for c in m.tool_calls) >= 2
        assert any(
            "incoming-messages" in m.content and second in m.content for m in history
        )
        if member["pane_id"]:
            (self.capture_dir / "member.txt").write_text(
                self.capture(member["pane_id"]), encoding="utf-8"
            )
        self.delete(team, member)
        self.record(
            backend=expected_backend,
            agent_id=member["agent_id"],
            pane=member["pane_id"],
            cwd=pwd.read_text().strip(),
            file_lines=path.read_text().splitlines(),
            lead_idle_read=True,
            autonomous_reply=True,
            cleanup=True,
        )

    def coordinator_case(self):
        self.launch("coordinator", coordinator=True)
        assert "[COORDINATOR]" in self.capture(self.lead)
        path = self.file("coord")
        prompt = f"请尝试用 write_file 写 hello world 到 {path}。"
        self.submit(prompt)
        self.wait_reply(prompt)
        assert not path.exists()
        history = load_session(str(self.archive))
        assert any(
            r.is_error and "write_file" in r.content
            for m in history
            for r in m.tool_results
        )
        prompt = "请用 bash 运行 git status。"
        self.submit(prompt)
        self.wait_reply(prompt)
        assert any(
            "nothing to commit" in r.content
            for m in load_session(str(self.archive))
            for r in m.tool_results
        )
        self.create("coord")
        self.worker = {"name": "reader", "path": None}
        member = self.spawn("coord", "reader", "阅读 README.md，把真实标题汇报给 Lead")
        self.until(
            lambda: any(
                "README.md" in m["text"] and m["read"] for m in self.mail("coord")
            ),
            "协调者收到队员报告",
        )
        self.until(
            lambda: (
                self.member("coord", "reader", idle=True)
                and self.replied("[team-update]")
            ),
            "协调场景结束",
        )
        history = load_session(str(self.archive))
        dispatch = next(
            i
            for i, m in enumerate(history)
            if any(c.name == "Agent" for c in m.tool_calls)
        )
        report = next(
            i
            for i, m in enumerate(history[dispatch + 1 :], dispatch + 1)
            if m.role == "user" and m.content.startswith("<team-update>")
        )
        assert not any(m.tool_calls for m in history[dispatch + 1 : report])
        self.delete("coord", member)
        self.record(write_denied=True, git_status=True, dispatch_and_report=True)

    def plan_case(self):
        self.launch("plan")
        self.create("plan-test")
        path = self.file("plan")
        self.worker = {"name": "planner", "path": path}
        member = self.spawn(
            "plan-test",
            "planner",
            f"制定 hello world 计划，批准后写入 {path}",
            "planner",
        )
        self.until(lambda: self.member("plan-test", "planner", idle=True), "等待计划")
        self.until(
            lambda: any(
                m.get("type") == "plan_approval_request" and m["read"]
                for m in self.mail("plan-test")
            ),
            "Lead 已收到计划",
        )
        assert not path.exists()
        self.wait_reply("[team-update]")
        prompt = "批准 planner 的最新计划，请用 SendMessage 回复带 request_id 的审批。"
        self.submit(prompt)
        self.wait_reply(prompt)
        self.until(
            lambda: path.is_file() and path.read_text() == "hello world", "审批后写入"
        )
        self.until(
            lambda: self.member("plan-test", "planner", idle=True),
            "计划场景结束",
        )
        messages = self.mail("plan-test", member["agent_id"])
        assert any(
            m.get("type") == "plan_approval_response" and m["approve"] for m in messages
        )
        assert any(
            "权限已切换到 default" in m.content
            for m in load_session(member["session_dir"])
        )
        (self.capture_dir / "member.txt").write_text(
            self.capture(member["pane_id"]), encoding="utf-8"
        )
        self.delete("plan-test", member)
        self.record(
            plan_before_approval=True,
            mode_after_approval="default",
            write_after_approval=True,
        )

    def response_for(self, body):
        messages = body["messages"]
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        tools = {d["function"]["name"] for d in body.get("tools", [])}
        assert "TeamCreate" not in tools or "<team-context>" not in system
        if "<team-context>" in system:
            return self.member_response(messages, tools)
        user_index, last = next(
            (i, m["content"])
            for i, m in reversed(list(enumerate(messages)))
            if m["role"] == "user" and not m["content"].startswith("<")
        )
        if any(m["role"] == "tool" for m in messages[user_index + 1 :]):
            return {"text": "操作完成，等待队员回报。"}
        if last.startswith("[team-update]"):
            return {"text": "已收到队员报告，继续跟进团队任务。"}
        if match := re.search(r"名为 (\S+) 的团队", last):
            return tool_call("TeamCreate", team_name=match[1])
        if match := re.search(
            r"team_name=(\S+)、name=(\S+)、subagent_type=(\S+) 派队员", last
        ):
            team, name, agent_type = match.groups()
            return tool_call(
                "Agent",
                team_name=team,
                name=name,
                subagent_type=agent_type,
                description="派发验收任务",
                prompt=last.split("执行：", 1)[1].split("。派出", 1)[0],
            )
        if "批准 planner" in last:
            request = next(
                m
                for m in reversed(self.mail("plan-test"))
                if m.get("type") == "plan_approval_request"
            )
            return tool_call(
                "SendMessage",
                to="planner",
                type="plan_approval_response",
                request_id=request["requestId"],
                approve=True,
                content="批准，请执行。",
            )
        if last.startswith("用 SendMessage 给"):
            return tool_call("SendMessage", to=self.worker["name"], content=last)
        if "write_file" in last:
            assert "<coordinator>" in system and "write_file" not in tools
            return tool_call(
                "write_file", path=str(self.file("coord")), content="hello world"
            )
        if "git status" in last:
            assert "bash" in tools
            return tool_call("bash", command="git status")
        return {"text": "已收到。"}

    def member_response(self, messages, tools):
        name = self.worker["name"]
        path = self.worker["path"]
        start = max(
            i
            for i, m in enumerate(messages)
            if m["role"] == "user" and not m["content"].startswith("<")
        )
        current = messages[start:]
        called = sum(m["role"] == "tool" for m in current)
        if name == "planner":
            approved = any(
                "权限已切换到 default" in (m.get("content") or "") for m in current
            )
            if not called and not approved:
                assert "write_file" not in tools and "SendMessage" in tools
                return tool_call(
                    "SendMessage",
                    to="lead",
                    type="plan_approval_request",
                    content=f"计划：向 {path} 写 hello world 后验证内容。",
                )
            if not called:
                assert "write_file" in tools
                return tool_call("write_file", path=str(path), content="hello world")
            return {"text": "计划阶段已结束。"}
        if not called:
            if name == "reader":
                return tool_call("read_file", path="README.md")
            assert "bash" in tools and "TaskCreate" in tools
            pwd, first, second = (
                self.worker[key] for key in ("pwd", "first", "second")
            )
            joined = " ".join((m.get("content") or "") for m in current)
            command = (
                f"echo {second} >> {path}"
                if f"echo {second} >>" in joined
                else f"echo {first} > {path} && pwd > {pwd}"
            )
            return tool_call("bash", command=command)
        if called == 1:
            return tool_call(
                "SendMessage",
                to="lead",
                content=f"{name} 报告：任务完成，已核对 README.md 或写入结果。",
            )
        return {"text": "已汇报。"}

    def close(self):
        try:
            super().close()
        finally:
            for path in self.temporary:
                path.unlink(missing_ok=True)
            self.workspace.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    run = TeamRun(parser.parse_args().live)
    try:
        run.lifecycle()
        if not run.live:
            run.lifecycle(inprocess=True)
            run.coordinator_case()
            run.plan_case()
    finally:
        print(run.evidence, flush=True)
        run.close()
