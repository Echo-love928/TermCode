"""通过真实 tmux 验收 ch13，--live 使用已有的智谱配置。"""

import argparse
import json
import shlex
import shutil
import sys
import time
from collections import deque
from uuid import uuid4

import yaml
from tmux_e2e import ROOT, TmuxRun

from termcode.session.load import load_session


def role(name, settings="", prompt="完成分配的任务。"):
    return f"---\nname: {name}\ndescription: 测试角色 {name}\n{settings}---\n{prompt}\n"


def tool_call(tool, **args):
    return {
        "calls": [
            {
                "id": uuid4().hex[:16],
                "name": tool,
                "input": json.dumps(args, ensure_ascii=False),
            }
        ]
    }


def delegate(prompt, subagent_type=None, **options):
    if subagent_type is not None:
        options["subagent_type"] = subagent_type
    return tool_call("Agent", prompt=prompt, description="验收委派", **options)


def last_prompt(body):
    for message in reversed(body["messages"]):
        if message["role"] == "user" and not message["content"].startswith(
            "<task-notification>"
        ):
            return message["content"].split("</fork_boilerplate>\n\n", 1)[-1]
    return ""


class SubAgentRun(TmuxRun):
    def __init__(self, live=False):
        super().__init__()
        self.live = live
        self.results = {
            "backend": "zhipu" if live else "local SSE fixture",
            "cases": [],
        }
        self.evidence = ROOT / ".cache/tmux-e2e" / self.socket
        self.responses = {}
        if not live:
            self.server.response_for = self.response_for

    def launch(
        self,
        session,
        *,
        roles=None,
        allowed=None,
        ask=(),
        timeout=None,
        hooks=None,
        skills=None,
        background=True,
        workdir=None,
    ):
        work = workdir or self.evidence / session
        config = work / ".termcode"
        (config / "agents").mkdir(parents=True, exist_ok=True)
        home = work / "home"
        home.mkdir(exist_ok=True)
        (work / "README.md").write_text(
            "# TermCode fixture\nREADME line 2\n", encoding="utf-8"
        )
        permission = work / "src/termcode/permission"
        permission.mkdir(parents=True, exist_ok=True)
        for source in (ROOT / "src/termcode/permission").glob("*.py"):
            shutil.copyfile(source, permission / source.name)
        for name, contents in (roles or {}).items():
            (config / "agents" / f"{name}.md").write_text(contents, encoding="utf-8")
        if skills:
            (config / "skills").mkdir()
            for name, contents in skills.items():
                (config / "skills" / f"{name}.md").write_text(
                    contents, encoding="utf-8"
                )
        (config / "settings.yaml").write_text(
            yaml.safe_dump(
                {
                    "permissions": {
                        "allow": allowed
                        if allowed is not None
                        else ["Agent", "TaskStop", "SendMessage"],
                        "ask": list(ask),
                    },
                }
            ),
            encoding="utf-8",
        )
        if hooks:
            (config / "hooks.yaml").write_text(
                yaml.safe_dump({"hooks": hooks}), encoding="utf-8"
            )
        (config / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "providers": [self.entry("openai", "local-subagent")],
                    "enable_subagent_background": background,
                }
            ),
            encoding="utf-8",
        )
        if self.live:
            bootstrap = """
import asyncio, sys
from urllib.parse import urlsplit
from termcode.cli import _run
from termcode.config import Config, load
provider = next(p for p in load(sys.argv[1]).providers if p.name == 'zhipu')
url = urlsplit(provider.base_url or '')
if url.scheme != 'https' or url.hostname != 'open.bigmodel.cn':
    raise SystemExit('验收配置与智谱端点不匹配')
asyncio.run(_run(Config([provider])))
"""
            command = [
                sys.executable,
                "-c",
                bootstrap,
                str(ROOT / ".termcode/config.yaml"),
            ]
        elif timeout is not None:
            command = [
                sys.executable,
                "-c",
                "from termcode.agent import agent_tool; "
                f"agent_tool.AUTO_BACKGROUND_SECONDS={timeout}; "
                "from termcode.cli import main; main()",
            ]
        else:
            command = [sys.executable, "-m", "termcode"]
        runner = work / "run.sh"
        runner.write_text(
            "export LANG=C.UTF-8 TERM=xterm-256color\n"
            f"export HOME={shlex.quote(str(home))}\n"
            f"export PYTHONPATH={shlex.quote(str(ROOT / 'src'))}\n"
            "while [ ! -f start.release ]; do sleep 0.05; done\n"
            f"{shlex.join(command)}\n"
            "status=$?\nprintf '\\nTERMCODE_EXIT=%s\\n' \"$status\"\n",
            encoding="utf-8",
        )
        (work / "start.release").unlink(missing_ok=True)
        self.tmux(
            "new-session",
            "-d",
            "-s",
            session,
            "-x",
            "180",
            "-y",
            "48",
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
        print(f"READY {session} {work}", flush=True)
        return work

    def submit(self, session, prompt):
        self.send(session, prompt)
        time.sleep(0.2)
        self.key(session, "Enter")

    def messages(self, work):
        folders = sorted((work / ".termcode/sessions").glob("20*"))
        try:
            return load_session(str(folders[-1])) if folders else []
        except FileNotFoundError:
            return []

    def results_for(self, work, tool):
        history = self.messages(work)
        ids = {
            call.id for msg in history for call in msg.tool_calls if call.name == tool
        }
        return [
            result
            for msg in history
            for result in msg.tool_results
            if result.tool_call_id in ids
        ]

    def wait_result(self, session, work, tool, index=0):
        self.wait(
            lambda: len(self.results_for(work, tool)) > index, f"{tool} 结果", 100
        )
        return self.results_for(work, tool)[index]

    def wait_answer(self, session, work, prompt):
        def answered():
            messages = self.messages(work)
            last_user = next(
                (msg.content for msg in reversed(messages) if msg.role == "user"), ""
            )
            return (
                last_user == prompt
                and messages[-1].role == "assistant"
                and bool(messages[-1].content)
                and not messages[-1].tool_calls
            )

        self.wait(answered, "主 Agent 完成回复", 160)
        self.idle(session)

    def idle(self, session):
        def is_idle():
            pane = self.capture(session)
            return "Send a message" in pane and not any(
                marker in pane for marker in ("Imagining…", "Running…", "是否继续?")
            )

        self.wait(is_idle, "输入区可用", 100)

    def response_for(self, body):
        responses = self.responses.get(last_prompt(body))
        return responses.popleft() if responses else {"text": "验收请求已处理。"}

    def script(self, prompt, *responses):
        self.responses[prompt] = deque(responses)

    def requests_for(self, prompt):
        return [
            entry["body"]
            for entry in self.server.requests
            if last_prompt(entry["body"]) == prompt
        ]

    def last_tool_result(self, prompt):
        return next(
            message["content"]
            for message in reversed(self.requests_for(prompt)[-1]["messages"])
            if message["role"] == "tool"
        )

    def local_turn(self, session, prompt, *responses, approve=None):
        self.script(prompt, *responses, {"text": f"验收完成：{prompt}"})
        self.submit(session, prompt)
        if approve:
            self.wait(
                lambda: (
                    approve in self.capture(session)
                    and "是否继续?" in self.capture(session)
                ),
                "审批弹窗",
            )
            self.snapshot(session, "approval-" + uuid4().hex[:6])
            self.key(session, "1")
        self.wait(
            lambda: f"验收完成：{prompt}" in self.capture(session),
            f"回复 {prompt}",
            100,
        )
        self.idle(session)

    def notification(self, session, task_id, status, label):
        prompt = f"请报告刚收到的 {label} 任务通知。"
        self.local_turn(session, prompt)
        notice = next(
            (
                message["content"]
                for message in self.requests_for(prompt)[0]["messages"]
                if message["content"]
                and f"Task {task_id}" in message["content"]
                and "<task-notification>" in message["content"]
            ),
            "",
        )
        assert status in notice, notice
        assert "<task-notification>" not in self.capture(session)
        return notice

    def snapshot(self, session, label):
        pane = self.capture(session)
        folder = self.evidence / session
        folder.mkdir(exist_ok=True)
        (folder / f"{label}.txt").write_text(pane, encoding="utf-8")
        return pane

    def finish_session(self, session):
        self.idle(session)
        self.submit(session, "/exit")
        self.wait(lambda: "TERMCODE_EXIT=0" in self.capture(session), "正常退出", 60)
        self.snapshot(session, "exit")

    def record(self, scenario, **evidence):
        self.results["cases"].append({"scenario": scenario, **evidence})
        (self.evidence / "results.json").write_text(
            json.dumps(self.results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"PASS {scenario}", flush=True)

    def run(self):
        session = "ch13-local"
        roles = {
            "auto-bash": role("auto-bash", "tools: [bash]\npermissionMode: dontAsk\n"),
            "ask-bash": role("ask-bash", "tools: [bash]\n"),
            "wc-counter": role(
                "wc-counter",
                "tools: [bash]\ndisallowedTools: [write_file, edit_file]\n"
                "permissionMode: dontAsk\nmaxTurns: 5\n",
                "先输出 [wc-counter] 再报告行数。",
            ),
            "bad": role("bad", "model: gpt-4\npermissionMode: weirdMode\n"),
            "limited": role("limited", "maxTurns: 1\n"),
        }
        hooks = [
            {
                "name": event,
                "event": event,
                "if": {
                    "all_of": [
                        {
                            "field": "tool_name",
                            "match": {"type": "exact", "value": "grep"},
                        }
                    ]
                },
                "action": {"type": "shell", "command": f"echo {event} >> hooks.log"},
            }
            for event in ("PreToolUse", "PostToolUse")
        ]
        skills = {
            "inline-test": "---\nname: inline-test\ndescription: 测试\n"
            "---\nINLINE_SKILL_MARKER",
            "fork-test": "---\nname: fork-test\ndescription: 测试\nmode: fork\n"
            "context: none\n---\n请读取 README.md。",
        }
        count_command = "wc -l src/termcode/permission/*.py"
        work = self.launch(
            session,
            roles=roles,
            hooks=hooks,
            skills=skills,
            allowed=[
                "Agent",
                "TaskStop",
                "SendMessage",
                "Bash(=sleep 20)",
                f"Bash(={count_command})",
            ],
            ask=["Agent(=wc-counter)"],
        )
        files = list((work / "src/termcode/permission").glob("*.py"))
        original = {p.name: p.read_bytes() for p in files}
        self._roles_and_skills(session, work, original)
        self._tasks(
            session,
            work,
            count_command,
            sum(data.count(b"\n") for data in original.values()),
        )
        self._permissions(session, work)
        self.finish_session(session)
        self._detach_and_config()

    def _roles_and_skills(self, session, work, original):
        prompt = "请用 Explore 搜索权限包并统计 test_ 函数数量。"
        child = "统计权限包的 test_ 函数数量。"
        self.script(
            child,
            tool_call(
                "grep",
                path="src/termcode/permission",
                pattern=r"^\s*def test_",
                glob="*.py",
            ),
            {"text": "找到 0 个 test_ 函数。"},
        )
        self.local_turn(session, prompt, delegate(child, "Explore"))
        request = self.requests_for(child)[0]
        names = {tool["function"]["name"] for tool in request["tools"]}
        assert not ({"Agent", "write_file", "edit_file"} & names)
        main_names = {
            tool["function"]["name"] for tool in self.requests_for(prompt)[0]["tools"]
        }
        assert {"Agent", "TaskList", "TaskGet", "TaskStop", "SendMessage"} <= main_names
        assert self.last_tool_result(child) == "无命中"
        assert "0 个" in self.last_tool_result(prompt) and "Agent(" in self.snapshot(
            session, "explore"
        )
        assert original == {
            p.name: p.read_bytes()
            for p in (work / "src/termcode/permission").glob("*.py")
        }
        assert (work / "hooks.log").read_text().splitlines() == [
            "PreToolUse",
            "PostToolUse",
        ]
        self.record(
            "1 前台 Explore 和子 Agent Hook",
            tools=sorted(names),
            result=self.last_tool_result(prompt),
        )

        prompt, child = "让 Explore 尝试再叫 Plan。", "请尝试再调用 Plan 子 Agent。"
        self.script(child, {"text": "当前工具不包含 Agent，无法再次委派。"})
        self.local_turn(session, prompt, delegate(child, "Explore"))
        assert "Agent" not in {
            t["function"]["name"] for t in self.requests_for(child)[0]["tools"]
        }
        assert "无法再次委派" in self.last_tool_result(prompt)
        self.record("7 定义式嵌套阻断")

        prompt = "请使用 inline-test Skill 回答。"
        self.local_turn(session, prompt, tool_call("LoadSkill", name="inline-test"))
        assert (
            "INLINE_SKILL_MARKER"
            in self.requests_for(prompt)[-1]["messages"][0]["content"]
        )
        prompt, child = (
            "请使用 fork-test Skill 读取 README。",
            "请读取 README.md。\n\n## User Request\n\n"
            "请使用 fork-test Skill 读取 README。",
        )
        self.script(
            child,
            tool_call("read_file", path="README.md"),
            {"text": "Skill fork 已读 README。"},
        )
        self.local_turn(session, prompt, tool_call("LoadSkill", name="fork-test"))
        assert "Skill fork 已读 README" in self.last_tool_result(prompt)
        assert (
            "INLINE_SKILL_MARKER"
            not in self.requests_for(child)[0]["messages"][0]["content"]
        )
        self.record("Skill inline 和 fork 兼容")

        prompt, child = "请调用 bad 子 Agent 回 hi。", "回个 hi。"
        self.script(child, {"text": "hi from bad"})
        self.local_turn(session, prompt, delegate(child, "bad"))
        raw = (work / "terminal.raw").read_text(encoding="utf-8", errors="replace")
        assert (
            'unknown model "gpt-4"' in raw
            and 'unknown permissionMode "weirdMode"' in raw
        )
        assert "hi from bad" in self.last_tool_result(prompt)
        assert self.requests_for(child)[0]["model"] == "test-model"
        (work / ".termcode/agents/bad.md").unlink()
        self.record("11.6 非法字段告警及降级")

    def _tasks(self, session, work, count_command, line_count):
        prompt = "请读取 README，给 Fork 留下上下文。"
        self.local_turn(session, prompt, tool_call("read_file", path="README.md"))
        prompt, child = (
            "Fork 子 Agent 统计工作区里的 Python 文件总行数。",
            "统计工作区 Python 文件总行数。",
        )
        self.script(
            child,
            tool_call("bash", command=count_command),
            {"text": f"Scope: 总计 {line_count} 行。", "delay": 3},
        )
        self.local_turn(session, prompt, delegate(child))
        launched = json.loads(self.last_tool_result(prompt))
        assert launched["status"] == "async_launched"
        self.submit(session, "/status")
        self.wait(lambda: "TermCode Status" in self.capture(session), "/status 响应")
        self.wait(lambda: len(self.requests_for(child)) == 2, "Fork 完成工具调用")
        body = self.requests_for(child)[0]
        assert body["messages"][0] == self.requests_for(prompt)[0]["messages"][0]
        assert "<fork_boilerplate>" in body["messages"][-1]["content"]
        assert any(msg["content"] == "[forked, skipped]" for msg in body["messages"])
        assert f"{line_count} total" in self.last_tool_result(child)
        self._wait_completed(session, launched["task_id"], "fork")
        notice = self._find_notice(
            launched["task_id"], "completed"
        ) or self.notification(session, launched["task_id"], "completed", "Fork")
        assert str(line_count) in notice
        self.record("2 Fork 后台与通知", notification=notice)

        prompt, child = (
            "后台启动通用 Agent，读取文件并继续长任务。",
            "执行一个可取消的长任务。",
        )
        self.script(
            child,
            tool_call("read_file", path="README.md"),
            tool_call("bash", command="sleep 20"),
            {"text": "已结束"},
        )
        self.local_turn(
            session,
            prompt,
            delegate(child, "general-purpose", run_in_background=True, name="long-run"),
        )
        task_id = json.loads(self.last_tool_result(prompt))["task_id"]
        self.wait(lambda: len(self.requests_for(child)) >= 2, "后台任务已运行工具")
        prompt = "用 TaskList 查询后台任务。"
        self.local_turn(session, prompt, tool_call("TaskList"))
        item = next(
            task
            for task in json.loads(self.last_tool_result(prompt))
            if task["id"] == task_id
        )
        assert (
            item["status"] == "running"
            and item["name"] == "long-run"
            and item["tool_count"] > 0
        )
        prompt = "用 TaskGet 查询长任务详情。"
        self.local_turn(session, prompt, tool_call("TaskGet", task_id=task_id))
        task = json.loads(self.last_tool_result(prompt))
        assert (
            task["status"] == "running"
            and task["start_time"] > 0
            and task["last_activity"] == "bash"
        )
        self.record("3 TaskList 和 TaskGet", task=task)
        prompt = "请用 TaskStop 停止长任务。"
        self.local_turn(session, prompt, tool_call("TaskStop", task_id=task_id))
        assert (
            json.loads(self.last_tool_result(prompt))["status"]
            == "cancellation_requested"
        )
        cancelled = self._wait_completed(session, task_id, "cancel", status="cancelled")
        notice = self._find_notice(task_id, "cancelled") or self.notification(
            session, task_id, "cancelled", "取消"
        )
        self.record("4 TaskStop 取消", task=cancelled, notification=notice)

        prompt, child = (
            "Fork 后请子 Agent 再尝试 Fork。",
            "请再 fork 一个子 Agent 阅读 README。",
        )
        self.script(
            child, delegate("不得被执行的嵌套任务"), {"text": "Scope: 已收到嵌套拦截。"}
        )
        self.local_turn(session, prompt, delegate(child))
        self.wait(
            lambda: bool(self.requests_for(child)), "Fork 子 Agent 已发出模型请求"
        )
        assert any(
            tool["function"]["name"] == "Agent"
            for tool in self.requests_for(child)[0]["tools"]
        )
        task_id = json.loads(self.last_tool_result(prompt))["task_id"]
        self._wait_completed(session, task_id, "nested")
        assert "Fork 子 Agent 不能再启动 Agent" in self.last_tool_result(child)
        self.record("8 Fork 嵌套阻断", result=self.last_tool_result(child))

        prompt, child = (
            "请后台启动 worker1 并读取 README 第一行。",
            "读取 README 第一行。",
        )
        self.script(
            child,
            tool_call("read_file", path="README.md"),
            {"text": "# TermCode fixture", "delay": 2},
        )
        self.local_turn(
            session,
            prompt,
            delegate(child, "general-purpose", name="worker1", run_in_background=True),
        )
        task_id = json.loads(self.last_tool_result(prompt))["task_id"]
        self._wait_completed(session, task_id, "worker1")
        notice = self._find_notice(task_id, "completed") or self.notification(
            session, task_id, "completed", "worker1"
        )
        assert "TermCode fixture" in notice
        followup = "继续读取 README 第二行。"
        self.script(
            followup,
            tool_call("read_file", path="README.md"),
            {"text": "README line 2", "delay": 2},
        )
        prompt = "用 SendMessage 给 worker1 续派读取 README 第二行的任务。"
        self.local_turn(
            session, prompt, tool_call("SendMessage", name="worker1", message=followup)
        )
        assert json.loads(self.last_tool_result(prompt)) == {
            "status": "resumed",
            "task_id": task_id,
        }
        task = self._wait_completed(session, task_id, "worker1-resumed")
        assert task["result"] == "README line 2"
        assert "README line 2" in self.last_tool_result(followup)
        assert any(
            message["content"] == "# TermCode fixture"
            for message in self.requests_for(followup)[0]["messages"]
        )
        notice = self._find_notice(
            task_id, "completed", "README line 2"
        ) or self.notification(session, task_id, "completed", "worker1 续派")
        assert "README line 2" in notice
        self.record("9 SendMessage 续派", task=task)

    def _find_notice(self, task_id, status, result=""):
        return next(
            (
                message["content"]
                for entry in self.server.requests
                for message in entry["body"]["messages"]
                if message["role"] == "user"
                and "<task-notification>" in message["content"]
                and f"Task {task_id}" in message["content"]
                and status in message["content"]
                and result in message["content"]
            ),
            "",
        )

    def _wait_completed(self, session, task_id, label, status="completed"):
        for attempt in range(6):
            prompt = f"查询 {label} 任务终态，第 {attempt + 1} 次。"
            self.local_turn(session, prompt, tool_call("TaskGet", task_id=task_id))
            task = json.loads(self.last_tool_result(prompt))
            if task["status"] == status:
                return task
            time.sleep(1)
        raise AssertionError(f"{label} 状态未达 {status}：{task}")

    def _permissions(self, session, work):
        for role_name, output in (
            ("auto-bash", "hello-from-subagent"),
            ("ask-bash", "from-ask-bash"),
        ):
            prompt, child = f"请调用 {role_name} 执行 echo。", f"运行 echo {output}。"
            self.script(
                child, tool_call("bash", command=f"echo {output}"), {"text": output}
            )
            self.local_turn(
                session,
                prompt,
                delegate(child, role_name),
                approve="[来自 SubAgent ask-bash]" if role_name == "ask-bash" else None,
            )
            assert output in self.last_tool_result(
                child
            ) and output in self.last_tool_result(prompt)
            self.record(f"权限 {role_name}", result=output)

        child = "用 wc 统计 README 和 cli.py 的行数。"
        prompt = "请用 wc-counter 统计 README 和 cli.py 的行数。"
        cli_path = "src/termcode/cli.py"
        shutil.copyfile(ROOT / cli_path, work / cli_path)
        lines = (work / cli_path).read_bytes().count(b"\n")
        self.script(
            child,
            tool_call("bash", command=f"wc -l README.md {cli_path}"),
            {"text": f"[wc-counter] README.md 2 行，cli.py {lines} 行。"},
        )
        self.local_turn(
            session, prompt, delegate(child, "wc-counter"), approve="wc-counter"
        )
        assert [
            tool["function"]["name"] for tool in self.requests_for(child)[0]["tools"]
        ] == ["bash"]
        assert "[wc-counter]" in self.requests_for(child)[0]["messages"][0]["content"]
        assert self.last_tool_result(prompt).startswith("[wc-counter]")
        assert f"{lines} {cli_path}" in self.last_tool_result(child)
        prompt, child = "让 limited 连续使用工具直至达到上限。", "持续读取 README。"
        self.script(
            child, tool_call("read_file", path="README.md"), {"text": "不应到达第二轮"}
        )
        self.local_turn(session, prompt, delegate(child, "limited"))
        assert len(
            self.requests_for(child)
        ) == 1 and "已达最大迭代轮数 1" in self.last_tool_result(prompt)
        self.record("11.5 自定义角色和最大轮数", result=self.last_tool_result(prompt))

    def _detach_and_config(self):
        session = "ch13-detach"
        work = self.launch(
            session,
            timeout=5,
            roles={"explore": role("Explore", prompt="[project-level-explore]")},
            allowed=["Agent", "Bash(=sleep 8)"],
        )
        prompt, child = (
            "请用 Explore 读取 README 第一行。",
            "读取 README 第一行并加上项目标记。",
        )
        self.script(
            child,
            tool_call("read_file", path="README.md"),
            {"text": "[project-level-explore] # TermCode fixture"},
        )
        self.local_turn(session, prompt, delegate(child, "Explore"))
        assert (
            "[project-level-explore]"
            in self.requests_for(child)[0]["messages"][0]["content"]
        )
        assert "[project-level-explore]" in self.last_tool_result(prompt)
        self.record("11 项目角色覆盖")

        prompt, child = (
            "请启动前台子 Agent 等待，再说 hello。",
            "等待 8 秒再报告 hello。",
        )
        self.script(
            child, tool_call("bash", command="sleep 8"), {"text": "hello from timeout"}
        )
        self.local_turn(session, prompt, delegate(child, "general-purpose"))
        launched = json.loads(self.last_tool_result(prompt))
        assert launched["status"] == "timed_out_to_background"
        task = self._wait_completed(session, launched["task_id"], "timeout")
        assert (
            task["result"] == "hello from timeout"
            and len(self.requests_for(child)) == 2
        )
        notice = self._find_notice(task["id"], "completed") or self.notification(
            session, task["id"], "completed", "超时移交"
        )
        assert "hello from timeout" in notice
        self.record("10 超时移交同一子会话", task=task, notification=notice)

        prompt, child = (
            "前台启动子 Agent，准备按 ESC 移交。",
            "请求审批后输出 detached。",
        )
        self.script(
            child,
            tool_call("bash", command="echo detached"),
            {"text": "detached complete"},
        )
        self.script(
            prompt, delegate(child, "general-purpose"), {"text": "ESC 已切到后台。"}
        )
        self.submit(session, prompt)
        self.wait(
            lambda: "[来自 SubAgent general-purpose]" in self.capture(session),
            "前台子 Agent 审批",
        )
        self.key(session, "Escape")
        self.wait(
            lambda: self.messages(work)[-1].content == "ESC 已切到后台。",
            "ESC 移交结果",
        )
        self.wait(
            lambda: "[来自 SubAgent general-purpose]" in self.capture(session),
            "后台重新呈现审批",
        )
        self.snapshot(session, "esc-background-approval")
        self.key(session, "1")
        self.idle(session)
        launched = json.loads(self.last_tool_result(prompt))
        assert launched["status"] == "detached_to_background"
        task = self._wait_completed(session, launched["task_id"], "esc")
        assert task[
            "result"
        ] == "detached complete" and "\nstdout:\ndetached\n" in self.last_tool_result(
            child
        )
        self.record("ESC 手动移交和审批继续", task=task)
        self.finish_session(session)

        (work / ".termcode/agents/explore.md").unlink()
        session = "ch13-disabled"
        self.launch(session, background=False, workdir=work)
        prompt, child = "尝试 Fork。", "被禁用的 Fork。"
        self.local_turn(session, prompt, delegate(child))
        assert "后台禁用" in self.last_tool_result(prompt) and not self.requests_for(
            child
        )
        prompt = "尝试显式后台启动。"
        self.local_turn(
            session, prompt, delegate(child, "general-purpose", run_in_background=True)
        )
        assert "已禁用" in self.last_tool_result(prompt) and not self.requests_for(
            child
        )
        prompt, child = (
            "再次用内置 Explore 读取 README 第一行。",
            "读取 README 第一行。",
        )
        self.script(child, {"text": "# TermCode fixture"})
        self.local_turn(session, prompt, delegate(child, "Explore"))
        assert (
            "[project-level-explore]"
            not in self.requests_for(child)[0]["messages"][0]["content"]
        )
        self.record("关闭后台和撤销项目角色覆盖")
        self.finish_session(session)

    def run_live(self):
        session = "ch13-live"
        work = self.launch(session)
        baseline = {
            p.name: p.read_bytes()
            for p in (work / "src/termcode/permission").glob("*.py")
        }
        prompt = (
            "请调用 Agent 工具（subagent_type=Explore，保持前台），让子 Agent "
            "用 grep 搜索 src/termcode/permission 中以 test_ 开头的 Python 函数。"
            "只统计数量，不修改文件。收到子 Agent 结果后直接报告数量。"
        )
        self.submit(session, prompt)
        result = self.wait_result(session, work, "Agent")
        self.wait_answer(session, work, prompt)
        pane = self.snapshot(session, "explore")
        assert not result.is_error and "0" in result.content and "Agent(" in pane
        assert baseline == {
            p.name: p.read_bytes()
            for p in (work / "src/termcode/permission").glob("*.py")
        }
        self.record(
            "真实模型 Explore 前台",
            result=result.content,
            capture="ch13-live/explore.txt",
        )

        prompt = "请调用 read_file 读取 README.md，然后告诉我第一行。"
        self.submit(session, prompt)
        self.wait_result(session, work, "read_file")
        self.wait_answer(session, work, prompt)
        prompt = (
            "请调用 Agent 工具，留空 subagent_type，Fork 一个后台任务。"
            "让子 Agent 从之前的对话取出 README 第一行，按 Scope 格式回复。"
            "你只需回复工具返回的任务 ID。"
        )
        self.submit(session, prompt)
        result = self.wait_result(session, work, "Agent", 1)
        assert json.loads(result.content)["status"] == "async_launched"
        self.wait_answer(session, work, prompt)
        self.submit(session, "/status")
        self.wait(lambda: "TermCode Status" in self.capture(session), "/status 响应")
        self.snapshot(session, "fork-launched")
        task_id = json.loads(result.content)["task_id"]
        for index in range(3):
            prompt = (
                f"请调用 TaskGet 查询 {task_id}，报告任务状态和结果。"
                "若收到 task-notification 也请说明。"
            )
            self.submit(session, prompt)
            state = json.loads(
                self.wait_result(session, work, "TaskGet", index).content
            )
            self.wait_answer(session, work, prompt)
            if state["status"] != "running":
                break
            time.sleep(2)
        assert state["status"] == "completed" and "TermCode fixture" in state["result"]
        self.snapshot(session, "fork-completed")
        self.record(
            "真实模型 Fork 与 TaskGet",
            task_id=task_id,
            result=state["result"],
            capture="ch13-live/fork-completed.txt",
        )
        self.finish_session(session)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    options = parser.parse_args()
    run = SubAgentRun(live=options.live)
    try:
        run.run_live() if options.live else run.run()
    finally:
        run.close()
