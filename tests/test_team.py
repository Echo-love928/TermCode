"""覆盖团队状态、协作协议和跨进程持久化的关键回归。"""

import asyncio
import json
import os
import re
import subprocess
import time

import pytest

from termcode import coordinator
from termcode.agent import Agent, SessionRuntime
from termcode.agent.agent_tool import AgentTool
from termcode.agent.team_hook import TeammateContext
from termcode.agent.team_mailbox import ingest_team_mailbox
from termcode.config import Config, FeaturesConfig
from termcode.conversation import Conversation
from termcode.llm import StreamEvent, ToolCall
from termcode.permission import Decision, Mode, new_engine
from termcode.permission.sandbox import sandbox_ok
from termcode.session import load_session
from termcode.subagent import load_catalog
from termcode.task import Manager as TaskManager
from termcode.team import AgentNameRegistry, BackendType, Manager, TeammateInfo
from termcode.team.backend import SpawnRequest
from termcode.team.backend.detect import detect
from termcode.team.backend.iterm2 import Iterm2Backend
from termcode.team.backend.process import BackendError, build_member_cmd
from termcode.team.mailbox import Box, Message, MessageType
from termcode.team.manager import TeamHasActiveMembersError
from termcode.team.tasks import Filter, Patch, Status, Store, Task
from termcode.team.tools import TeamTool, collaboration_tools, lifecycle_tools
from termcode.tool import new_default_registry, with_cwd
from termcode.tool.filter import (
    TEAMMATE_ALLOWED_TOOLS,
    FilterParams,
    apply_agent_tool_filter,
)
from termcode.worktree import Manager as WorktreeManager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr("termcode.team.manager.detect", lambda: BackendType.IN_PROCESS)
    return Manager(
        tmp_path / "home", tmp_path, None, TaskManager(), AgentNameRegistry()
    )


@pytest.fixture
def team_agent(manager):
    repo = manager.root
    (repo / ".gitignore").write_text(".termcode/\n")
    for args in (
        ("init", "-q"),
        ("add", ".gitignore"),
        (
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "initial",
        ),
    ):
        subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)
    manager.wt_mgr = WorktreeManager(repo)

    class Provider:
        name = "team-test"
        model = "team-test"

        async def stream(self, request):
            yield StreamEvent(text="队员完成", done=True)

    registry = new_default_registry()
    tool = AgentTool(load_catalog(repo), manager.task_mgr, team_hook=manager)
    registry.register(tool)
    agent = Agent(Provider(), registry, engine=new_engine(str(repo))[0])
    return agent, tool


async def test_team_create_concurrent_reload_and_cleanup(manager):
    demo = await manager.create("demo", "")
    first, second = await asyncio.gather(
        manager.create("foo bar/baz"),
        manager.create("foo bar/baz"),
    )
    assert {first.sanitized_name, second.sanitized_name} == {
        "foo-bar-baz",
        "foo-bar-baz-2",
    }
    assert demo.config_path.is_file()
    other = Manager(
        manager.home_dir, manager.root, None, TaskManager(), AgentNameRegistry()
    )
    stale = other.require("demo")
    await demo.add_member(TeammateInfo("alice", "agent-123"))
    with pytest.raises(TeamHasActiveMembersError):
        await manager.delete("demo")
    await stale.set_member_active("alice", False)
    assert manager.require("demo").member("alice").is_active is False
    await manager.delete("demo")
    assert not demo.config_dir.exists()


async def test_corrupt_config_and_create_failure(manager, monkeypatch, caplog):
    corrupt = manager.directory / "corrupt"
    corrupt.mkdir()
    (corrupt / "config.json").write_text("invalid")
    restarted = Manager(
        manager.home_dir, manager.root, None, TaskManager(), AgentNameRegistry()
    )
    assert restarted.get("corrupt") is None
    assert "跳过损坏的团队配置" in caplog.text

    def fail(*_):
        raise OSError("disk full")

    monkeypatch.setattr("termcode.team.types.atomic_json", fail)
    with pytest.raises(OSError, match="disk full"):
        await manager.create("failed")
    assert not (manager.directory / "failed").exists()


async def test_mailbox_stale_concurrent_and_busy(manager, monkeypatch):
    team = await manager.create("mail")
    box = Box(team.mailbox_dir)
    lock = team.mailbox_dir / "alice.lock"
    lock.touch()
    stale = time.time() - 11
    os.utime(lock, (stale, stale))
    original = Message(
        "lead", "hello", type=MessageType.SHUTDOWN_REQUEST, request_id="req-1"
    )
    await box.write("alice", original)
    await asyncio.gather(
        *(box.write("alice", Message("lead", str(i))) for i in range(10))
    )
    messages = await box.read("alice")
    assert messages[0] == original and len(messages) == 11
    await box.mark_read("alice", [0, 1])
    assert (await box.read_unread("alice"))[0] == list(range(2, 11))
    lock.touch()
    monkeypatch.setattr("termcode.team.persistence.random.uniform", lambda *_: 0)
    with pytest.raises(TimeoutError):
        await box.write("alice", Message("lead", "unsent"))
    assert len(await box.read("alice")) == 11


async def test_shared_tasks_maintain_reciprocal_dependencies(manager):
    team = await manager.create("tasks")
    store = Store(team.config_dir)
    before = await store.create(Task("调研"))
    after = await store.create(Task("实现"))
    assert re.fullmatch(r"task_[a-f0-9]{6}", after)
    await store.update(after, Patch(add_blocked_by=[before, before]))
    assert (await store.get(before)).blocks == [after]
    assert (await store.get(after)).blocked_by == [before]
    ready = {
        item["id"]: item["is_ready"]
        for item in await store.list_(Filter(Status.PENDING))
    }
    assert ready == {before: True, after: False}
    await store.update(before, Patch(status=Status.COMPLETED))
    assert (await store.list_(Filter(Status.PENDING)))[0]["is_ready"] is True
    with pytest.raises(ValueError):
        await store.update(before, Patch(add_blocks=["missing"]))
    assert (await store.get(before)).blocks == [after]
    await store.update(before, Patch(remove_blocks=[after]))
    assert not (await store.get(after)).blocked_by


async def test_fork_teammate_persists_history_and_resumes(manager, team_agent):
    agent, tool = team_agent
    manager.fork_enabled = True
    team = await manager.create("fork")
    conv = Conversation()
    conv.add_user("Lead 的已有对话")
    conv.add_assistant("已记录上下文")
    result = await tool.execute_for_turn(
        '{"team_name":"fork","name":"alice","prompt":"首次派发","description":"测试"}',
        agent,
        conv,
        Mode.DEFAULT,
        lambda _: None,
    )
    assert not result.is_error, result.content
    info = json.loads(result.content)
    agent_id = info["agent_id"]
    entry = manager.task_mgr.get(agent_id)
    member = team.member(agent_id)
    assert entry.sub_agent.teammate.backend_type == BackendType.IN_PROCESS
    assert member.agent_type == ""
    try:
        await entry.handle
        async with asyncio.timeout(3):
            while manager.require("fork").member(agent_id).is_active is not False:
                await asyncio.sleep(0.01)
        assert load_session(member.session_dir)[0].content == "Lead 的已有对话"
        mail = await Box(team.mailbox_dir).read("lead")
        assert any(m.text.startswith("[idle]") for m in mail)
        await manager.deliver(team, "lead", "alice", Message("lead", "继续验证"))
        await entry.handle
        assert any("继续验证" in m.content for m in load_session(member.session_dir))
    finally:
        await manager.delete("fork", force=True)
        await manager.task_mgr.clear()


async def test_failed_tmux_spawn_rolls_back_worktree_and_mailbox(
    manager, team_agent, monkeypatch
):
    monkeypatch.setattr("termcode.team.manager.detect", lambda: BackendType.TMUX)
    monkeypatch.setenv("TMUX", "fixture")
    commands = []

    async def fail(*cmd):
        commands.append(cmd)
        raise BackendError("no space for new pane")

    monkeypatch.setattr("termcode.team.backend.tmux.run", fail)
    team = await manager.create("failure")
    agent, tool = team_agent
    result = await tool.execute_for_turn(
        '{"team_name":"failure","name":"alice","prompt":"验证","description":"测试"}',
        agent,
        Conversation(),
        Mode.DEFAULT,
        lambda _: None,
    )
    assert result.is_error and "no space for new pane" in result.content
    assert commands[0][1] == "split-window"
    assert [m.name for m in manager.require("failure").members] == ["lead"]
    assert not list(team.mailbox_dir.iterdir())
    assert manager.wt_mgr.get("team-failure/alice") is None
    assert not list((manager.root / ".termcode/sessions").iterdir())


def test_registry_detection_and_tool_filter(monkeypatch):
    registry = AgentNameRegistry()
    registry.register("alice", "agent-123")
    assert registry.resolve("alice") == "agent-123"
    assert registry.name_of("agent-123") == "alice"
    registry.register("alice", "agent-456")
    assert registry.name_of("agent-123") is None
    registry.unregister("alice", "agent-123")
    assert registry.resolve("agent-456") == "agent-456"

    monkeypatch.setattr("termcode.team.backend.detect.shutil.which", lambda _: None)
    monkeypatch.setenv("TMUX", "socket")
    assert detect() == BackendType.TMUX
    monkeypatch.delenv("TMUX")
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    assert detect() == BackendType.IN_PROCESS
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.setattr("termcode.team.backend.detect.shutil.which", lambda name: name)
    assert detect() == BackendType.ITERM2
    assert set(BackendType) == {"tmux", "iterm2", "in-process"}

    tools = ["read_file", *TEAMMATE_ALLOWED_TOOLS]
    assert apply_agent_tool_filter(FilterParams(tools, 0, False)) == ["read_file"]
    assert (
        apply_agent_tool_filter(FilterParams(tools, 0, False, teammate=True)) == tools
    )


@pytest.mark.parametrize("feature", [False, True])
@pytest.mark.parametrize("environment", [False, True])
def test_coordinator_requires_two_flags(monkeypatch, feature, environment):
    monkeypatch.setenv("TERMCODE_COORDINATOR_MODE", "YES" if environment else "0")
    cfg = Config(features=FeaturesConfig(coordinator_mode=feature))
    assert coordinator.is_enabled(cfg) is (feature and environment)
    assert "bash" in coordinator.allowed_tools()
    assert {"write_file", "edit_file"}.isdisjoint(coordinator.allowed_tools())


async def test_tools_require_context_and_plan_approval_is_correlated(
    manager, monkeypatch
):
    registry = new_default_registry().overlay(lifecycle_tools(manager))
    assert registry.get("TaskCreate") is None
    assert (await TeamTool("TaskCreate", manager).execute('{"title":"oops"}')).is_error
    created = await registry.execute("TeamCreate", '{"team_name":"plan"}')
    assert not created.is_error
    team = manager.require("plan")
    await team.add_member(
        TeammateInfo("alice", "agent-123", pane_id="%7", backend_type=BackendType.TMUX)
    )
    lead_registry = manager.lead_registry(registry)
    assert set(TEAMMATE_ALLOWED_TOOLS).issubset(
        d.name for d in lead_registry.definitions()
    )
    box = Box(team.mailbox_dir)
    ctx = TeammateContext("plan", "alice", "agent-123", BackendType.TMUX, box)
    alice = registry.overlay(
        collaboration_tools(manager, team_name="plan", teammate=ctx)
    )
    request = await alice.execute(
        "SendMessage",
        json.dumps(
            {
                "to": "lead",
                "content": "实现计划",
                "type": "plan_approval_request",
            }
        ),
    )
    request_id = json.loads(request.content)["request_id"]

    class Pane:
        async def alive(self, *_):
            return True

        async def wake(self, *_):
            pass

    monkeypatch.setattr(manager, "backend", lambda _: Pane())
    response = {
        "to": "alice",
        "content": "可以执行",
        "type": "plan_approval_response",
        "approve": True,
        "request_id": "unrelated",
    }
    assert (await lead_registry.execute("SendMessage", json.dumps(response))).is_error
    response["request_id"] = request_id
    assert not (
        await lead_registry.execute("SendMessage", json.dumps(response))
    ).is_error
    assert (await lead_registry.execute("SendMessage", json.dumps(response))).is_error
    agent = object.__new__(Agent)
    agent.runtime = SessionRuntime()
    agent.permission_mode = Mode.PLAN
    conv = Conversation()
    await ingest_team_mailbox(agent, ctx, conv)
    assert agent.permission_mode == Mode.DEFAULT
    assert "<incoming-messages>" in conv.messages()[0].content
    assert not (await box.read_unread("agent-123"))[0]


async def test_iterm2_command_contract(monkeypatch):
    commands = []

    async def run(*cmd):
        commands.append(cmd)
        if cmd[2] == "split":
            return "Created new pane: pane-123"
        if cmd[2] == "list":
            return '[{"id":"pane-123"}]'
        return ""

    monkeypatch.setattr("termcode.team.backend.iterm2.run", run)
    request = SpawnRequest(
        "demo",
        "alice",
        "agent-123",
        "/work tree",
        "/session",
        "planner",
        "",
        "private prompt",
        True,
        "/project",
    )
    cmd = build_member_cmd(request)
    assert cmd[cmd.index("--agent-id") + 1] == "agent-123"
    assert "private prompt" not in cmd
    backend = Iterm2Backend()
    assert await backend.spawn(request) == ("pane-123", "agent-123")
    await backend.wake("pane-123", "agent-123")
    await backend.kill("pane-123", "agent-123")
    assert [cmd[2] for cmd in commands] == [
        "split",
        "run",
        "list",
        "send",
        "list",
        "close",
    ]
    assert "--agent-id agent-123" in commands[1][-1]


@pytest.mark.skipif(os.name != "posix", reason="POSIX 临时目录")
def test_temp_file_allowlist_preserves_worktree_isolation(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    worktree = root / "worktree"
    worktree.mkdir()
    engine = new_engine(str(root))[0]
    engine.allow_temp_files = True
    assert sandbox_ok(engine, "/tmp/termcode-ch15.txt")
    assert not sandbox_ok(engine, "/etc/passwd")
    with with_cwd(str(worktree)):
        for name in ("write_file", "edit_file"):
            args = json.dumps({"path": "/tmp/termcode-ch15.txt"})
            assert (
                engine.check(Mode.BYPASS, ToolCall("tool", name, args))[0]
                == Decision.ALLOW
            )
        assert (
            engine.check(
                Mode.BYPASS,
                ToolCall("tool", "write_file", json.dumps({"path": "../outside"})),
            )[0]
            == Decision.DENY
        )
