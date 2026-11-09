import asyncio
from pathlib import Path

import pytest
from test_subagents import Provider, call, payload, role_file
from test_worktree_manager import repo as repo

from termcode.agent import Agent
from termcode.agent.agent_tool import AgentTool
from termcode.cli import _run
from termcode.config import Config, ProviderConfig
from termcode.llm import StreamEvent
from termcode.permission import new_engine
from termcode.subagent import Source, load_catalog, parse_definition
from termcode.task import Manager as TaskManager
from termcode.tool import new_default_registry
from termcode.tui.app import TermCodeApp
from termcode.worktree import Manager


@pytest.mark.parametrize(
    "value,expected", [("", ""), ("worktree", "worktree"), ("bad", "")]
)
def test_isolation_frontmatter(value, expected, capsys):
    line = f"isolation: {value}\n" if value else ""
    definition = parse_definition(
        f"---\nname: writer\ndescription: fixture\n{line}---\n".encode(),
        "writer.md",
        Source.PROJECT,
    )
    assert definition.isolation == expected
    assert ("isolation 值无效" in capsys.readouterr().err) == (value == "bad")


async def test_agent_worktree_foreground_and_tool_writes(repo, monkeypatch):
    monkeypatch.chdir(repo)
    role_file(
        repo,
        "writer.md",
        "---\nname: writer\ndescription: fixture\n"
        "isolation: worktree\nbackground: true\npermission_mode: dontAsk\n---\n写文件",
    )
    provider = Provider(
        call("write_file", path="scratch.txt", content="SUBAGENT"),
        StreamEvent(text="已写入", done=True),
        call("read_file", path="README.md"),
        StreamEvent(text="已阅读", done=True),
    )
    registry = new_default_registry()
    task_mgr = TaskManager()
    catalog = load_catalog(repo)
    agent = Agent(provider, registry, engine=new_engine(str(repo))[0])
    disabled = AgentTool(catalog, task_mgr, agent)
    assert (await disabled.execute(payload("writer"))).is_error

    wt_mgr = Manager(repo)
    agent_tool = AgentTool(
        catalog, task_mgr, agent, bg_enabled=False, worktree_mgr=wt_mgr
    )
    (repo / "scratch.txt").write_text("MAIN", encoding="utf-8")
    result = await agent_tool.execute(payload("writer", run_in_background=True))
    assert not result.is_error, result.content
    assert "已写入" in result.content and "Worktree 保留" in result.content
    assert not task_mgr.list()
    (retained,) = wt_mgr.list()
    assert (Path(retained.path) / "scratch.txt").read_text() == "SUBAGENT"
    assert (repo / "scratch.txt").read_text() == "MAIN"
    user_prompt = next(
        msg.content for msg in provider.requests[0].messages if msg.role == "user"
    )
    assert "<worktree-context>" in user_prompt
    assert str(repo) in user_prompt and retained.path in user_prompt
    read = await agent_tool.execute(payload("writer"))
    assert not read.is_error and read.content == "已阅读"
    assert wt_mgr.list() == [retained]


@pytest.mark.parametrize("git_root", [False, True])
async def test_cli_worktree_startup_and_tool_list(repo, git_root, monkeypatch, capsys):
    home = repo / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(repo if git_root else home)
    swept = asyncio.Event()
    if git_root:
        manager = Manager(repo)
        wt = await manager.create("restore", "HEAD", manual=True)
        await manager.enter(wt.name)

    async def sweep(self, cutoff):
        swept.set()
        return []

    async def run_app(app, *, inline):
        assert inline
        tool = app._tool_registry.get("Agent")
        assert isinstance(tool, AgentTool) and tool.worktree_mgr is app.worktree_mgr
        assert (app.worktree_mgr is not None) == git_root
        assert {item.name for item in app._tool_registry.definitions()} == {
            "read_file",
            "write_file",
            "edit_file",
            "glob",
            "grep",
            "bash",
            "Agent",
            "TaskList",
            "TaskGet",
            "TaskStop",
            "SendMessage",
            "LoadSkill",
            "InstallSkill",
        }
        if git_root:
            assert app.active_cwd == wt.path
            await asyncio.wait_for(swept.wait(), 2)
        else:
            assert not app.active_cwd and not swept.is_set()

    monkeypatch.setattr(TermCodeApp, "run_async", run_app)
    monkeypatch.setattr(Manager, "sweep_stale", sweep)
    await _run(Config([ProviderConfig("test", "openai", "test-key", "test-model")]))
    assert ("Worktree 管理器降级" in capsys.readouterr().err) != git_root
