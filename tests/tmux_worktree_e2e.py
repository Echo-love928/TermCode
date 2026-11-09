"""通过真实 tmux、Git 和 TermCode CLI 验收 ch14 的六个场景。"""

import argparse
import subprocess
from pathlib import Path

from tmux_subagent_e2e import SubAgentRun, delegate, last_prompt, role, tool_call

WRITE = "只用 write_file 工具把相对路径 scratch_ch14.txt 的内容覆盖为 SUBAGENT。"
READ = "用 read_file 读 README.md 头 5 行，然后用 30 字总结。"


class WorktreeRun(SubAgentRun):
    def git(self, work, *args):
        return subprocess.check_output(
            ["git", "-C", str(work), *args], text=True
        ).strip()

    def start_case(self, name):
        work = self.evidence / name
        work.mkdir(parents=True)
        self.git(work, "init", "-b", "main")
        self.git(work, "config", "user.name", "TermCode E2E")
        self.git(work, "config", "user.email", "fixture@example.invalid")
        (work / "README.md").write_text(
            "# TermCode\n终端中的 AI 编程助手。\n", encoding="utf-8"
        )
        (work / ".gitignore").write_text(
            ".termcode/\nhome/\nrun.sh\nstart.release\nterminal.raw\n", encoding="utf-8"
        )
        self.git(work, "add", "README.md", ".gitignore")
        self.git(work, "commit", "-m", "测试基线")
        return self.launch(
            name,
            workdir=work,
            allowed=["Agent"],
            roles={
                "worktree-writer": role(
                    "worktree-writer",
                    "permission_mode: dontAsk\nmax_turns: 5\nisolation: worktree\n",
                )
            },
        )

    def response_for(self, body):
        prompt = last_prompt(body)
        if prompt.startswith("<worktree-context>"):
            assert "</worktree-context>" in prompt
            if body["messages"][-1]["role"] != "tool":
                return (
                    tool_call("write_file", path="scratch_ch14.txt", content="SUBAGENT")
                    if prompt.endswith(WRITE)
                    else tool_call("read_file", path="README.md")
                )
            result = body["messages"][-1]["content"]
            if prompt.endswith(WRITE):
                assert "写入" in result
                return {"text": "已在副本写入 SUBAGENT。"}
            assert "终端中的 AI 编程助手" in result
            return {"text": "TermCode 是终端中的 AI 编程助手。"}
        if body["messages"][-1]["role"] == "tool":
            return {"text": "已根据工具结果处理请求。"}
        if "worktree-writer" in prompt:
            return delegate(
                WRITE if "scratch_ch14.txt" in prompt else READ, "worktree-writer"
            )
        if "probe.txt" in prompt:
            return tool_call("read_file", path="probe.txt")
        raise AssertionError(prompt)

    def command(self, name, text, expected, label):
        previous = self.capture(name).count(expected)
        self.submit(name, text)
        self.wait(
            lambda: self.capture(name).count(expected) > previous,
            f"{text} → {expected}",
            100,
        )
        self.snapshot(name, label)

    def done(self, name, **evidence):
        self.finish_session(name)
        self.tmux("kill-session", "-t", name)
        self.record(name, **evidence)

    def run(self):
        name = "ch14-1-write"
        work = self.start_case(name)
        main = work / "scratch_ch14.txt"
        main.write_text("MAIN", encoding="utf-8")
        prompt = f"用 Agent 工具调 subagent_type=worktree-writer，任务是「{WRITE}」"
        self.submit(name, prompt)
        result = self.wait_result(name, work, "Agent")
        self.wait_answer(name, work, prompt)
        assert not result.is_error and "Worktree 保留" in result.content
        (wt,) = (work / ".termcode/worktrees").glob("agent-a*")
        assert main.read_text() == "MAIN"
        assert (wt / main.name).read_text().strip() == "SUBAGENT"
        assert f"worktree-{wt.name}" in result.content
        pane = self.snapshot(name, "agent-write")
        raw_pane = (work / "terminal.raw").read_text(encoding="utf-8", errors="replace")
        assert "Agent(" in raw_pane and "worktree" in pane.casefold()
        main.unlink()
        self.command(
            name, f"/worktree remove {wt.name} --discard", "已删除 Worktree", "cleanup"
        )
        assert not wt.exists()
        self.done(name, main="MAIN", worktree="SUBAGENT", agent_result=result.content)

        name = "ch14-2-read"
        work = self.start_case(name)
        prompt = f"用 Agent 工具调 subagent_type=worktree-writer，任务是「{READ}」"
        self.submit(name, prompt)
        result = self.wait_result(name, work, "Agent")
        self.wait_answer(name, work, prompt)
        assert (
            not result.is_error
            and result.content
            and "Worktree 保留" not in result.content
        )
        assert not list((work / ".termcode/worktrees").iterdir())
        assert not self.git(work, "branch", "--list", "worktree-agent-a*")
        self.snapshot(name, "agent-read")
        self.done(name, agent_result=result.content, worktree_count=0)

        name = "ch14-3-manual"
        work = self.start_case(name)
        self.command(name, "/worktree create demo-feature", "Worktree 已创建", "create")
        self.command(name, "/worktree list", "[manual]", "list")
        wt = work / ".termcode/worktrees/demo-feature"
        branch = self.git(wt, "branch", "--show-current")
        assert branch == "worktree-demo-feature" and (wt / "README.md").is_file()
        self.command(
            name, "/worktree remove demo-feature --discard", "已删除 Worktree", "remove"
        )
        assert not wt.exists() and not self.git(work, "branch", "--list", branch)
        self.done(name, branch=branch, removed=True)

        name = "ch14-4-protection"
        work = self.start_case(name)
        self.command(name, "/worktree create demo-feature", "Worktree 已创建", "create")
        wt = work / ".termcode/worktrees/demo-feature"
        (wt / "test.txt").write_text("modified", encoding="utf-8")
        self.command(
            name, "/worktree enter demo-feature", "已进入 demo-feature", "enter"
        )
        self.command(name, "/worktree exit --remove", "有未提交修改", "protected")
        assert (wt / "test.txt").read_text() == "modified"
        self.command(
            name,
            "/worktree exit --remove --discard",
            "已退出并删除 Worktree",
            "discard",
        )
        assert not wt.exists() and not self.git(
            work, "branch", "--list", "worktree-demo-feature"
        )
        assert (work / ".termcode/worktree_session.json").read_text() == "null"
        self.done(name, protected=True, removed_with_discard=True)

        name = "ch14-5-cwd"
        work = self.start_case(name)
        self.command(name, "/worktree create cwd-test", "Worktree 已创建", "create")
        wt = work / ".termcode/worktrees/cwd-test"
        (wt / "probe.txt").write_text("in-worktree-only", encoding="utf-8")
        self.command(name, "/worktree enter cwd-test", "已进入 cwd-test", "enter")
        prompt = "请仅调用一次 read_file 读取相对路径 probe.txt，然后报告工具结果。"
        self.submit(name, prompt)
        inside = self.wait_result(name, work, "read_file")
        self.wait_answer(name, work, prompt)
        assert not inside.is_error and "in-worktree-only" in inside.content
        self.snapshot(name, "read-inside")
        self.command(name, "/worktree exit", "已退出 Worktree", "exit-worktree")
        self.submit(name, prompt)
        outside = self.wait_result(name, work, "read_file", index=1)
        self.wait_answer(name, work, prompt)
        assert outside.is_error and "probe.txt" in outside.content
        self.snapshot(name, "read-outside")
        self.command(
            name, "/worktree remove cwd-test --discard", "已删除 Worktree", "remove"
        )
        self.done(name, inside=inside.content, outside=outside.content)

        name = "ch14-6-slug"
        work = self.start_case(name)
        self.command(
            name, "/worktree create ../etc", "拒绝 Worktree 名称", "reject-traversal"
        )
        self.command(name, "/worktree create ..", "拒绝 Worktree 名称", "reject-parent")
        assert not (work / ".termcode/etc").exists()
        assert not list((work / ".termcode/worktrees").iterdir())
        self.command(name, "/worktree create normal_one", "Worktree 已创建", "create")
        assert (work / ".termcode/worktrees/normal_one").is_dir()
        self.command(
            name, "/worktree remove normal_one --discard", "已删除 Worktree", "remove"
        )
        self.done(name, rejected=["../etc", ".."], valid="normal_one")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    runner = WorktreeRun(live=parser.parse_args().live)
    try:
        runner.run()
        print(f"验收证据：{Path(runner.evidence) / 'results.json'}")
    finally:
        runner.close()
