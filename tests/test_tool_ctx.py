import asyncio
import json
import os
from pathlib import Path

from termcode.llm import ToolCall
from termcode.permission import Decision, Mode, new_engine
from termcode.tool import cwd_from_ctx, new_default_registry, resolve_path, with_cwd


async def test_core_tool_cwd_and_schema(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subdir = tmp_path / "worktree"
    subdir.mkdir()
    (tmp_path / "a.txt").write_text("MAIN", encoding="utf-8")
    registry = new_default_registry()
    assert {
        tool.name: set(tool.input_schema["properties"])
        for tool in registry.definitions()
    } == {
        "read_file": {"path"},
        "write_file": {"path", "content"},
        "edit_file": {"path", "old_string", "new_string"},
        "bash": {"command"},
        "glob": {"pattern", "path"},
        "grep": {"pattern", "path", "glob"},
    }

    async def run(name, **args):
        result = await registry.execute(name, json.dumps(args))
        assert not result.is_error, result.content
        return result.content

    async def main_read():
        await asyncio.sleep(0)
        return await run("read_file", path="a.txt")

    main_task = asyncio.create_task(main_read())
    assert resolve_path("a.txt") == str(tmp_path / "a.txt")
    with with_cwd(str(subdir)):
        assert cwd_from_ctx() == str(subdir)
        assert resolve_path(str(tmp_path / "a.txt")) == str(tmp_path / "a.txt")
        await run("write_file", path="a.txt", content="before")
        await run("edit_file", path="a.txt", old_string="before", new_string="changed")
        assert "changed" in await run("read_file", path="a.txt")
        assert await run("glob", pattern="*.txt") == "a.txt"
        assert "changed" in await run("grep", pattern="changed")
        command = "cd" if os.name == "nt" else "pwd"
        assert str(subdir) in await run("bash", command=command)
        assert "MAIN" in await main_task
        assert Path.cwd() == tmp_path
    assert cwd_from_ctx() is None


def test_permissions_check_same_path_as_worktree_tools(tmp_path):
    engine, _ = new_engine(str(tmp_path))
    subdir = tmp_path / "worktree"
    subdir.mkdir()
    from termcode.permission.rule import parse_rule

    rule, _ = parse_rule("Read(private.txt)")
    assert rule is not None
    engine.project.deny.append(rule)
    with with_cwd(str(subdir)):
        for path in ("private.txt", str(subdir / "private.txt"), "../outside.txt"):
            call = ToolCall("test", "read_file", json.dumps({"path": path}))
            assert engine.check(Mode.DEFAULT, call, read_only=True)[0] == Decision.DENY
        call = ToolCall("test", "read_file", '{"path":"public.txt"}')
        assert engine.check(Mode.DEFAULT, call, read_only=True)[0] == Decision.ALLOW
