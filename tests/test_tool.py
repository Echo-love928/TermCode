"""工具真实文件操作、子进程、参数错误与体量边界。"""

import asyncio
import json
import os
import shlex
import subprocess
import sys

import pytest

from termcode.tool import Registry, Result, new_default_registry


def shell_python(code):
    command = [sys.executable, "-c", code]
    return subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)


async def execute(name, **args):
    return await new_default_registry().execute(name, json.dumps(args))


def test_registry_definitions_and_lookup():
    registry = new_default_registry()
    names = [d.name for d in registry.definitions()]
    assert names == ["read_file", "write_file", "edit_file", "bash", "glob", "grep"]
    assert all(registry.get(name) is not None for name in names)
    assert registry.get("missing") is None
    with pytest.raises(ValueError, match="重复"):
        registry.register(registry.get("read_file"))


async def test_read_file_numbered_and_errors(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("中文\nsecond", encoding="utf-8")
    result = await execute("read_file", path=str(path))
    assert not result.is_error
    assert "1\t中文" in result.content and "2\tsecond" in result.content
    assert (await execute("read_file", path=str(path / "missing"))).is_error
    assert (await execute("read_file", path=str(tmp_path))).is_error


async def test_write_create_overwrite_and_edit_counts(tmp_path):
    path = tmp_path / "a/b/c.txt"
    assert not (await execute("write_file", path=str(path), content="old old")).is_error
    assert path.read_text() == "old old"
    multi = await execute(
        "edit_file", path=str(path), old_string="old", new_string="new"
    )
    zero = await execute(
        "edit_file", path=str(path), old_string="missing", new_string="new"
    )
    assert multi.is_error and "2 处" in multi.content
    assert zero.is_error and "0 处" in zero.content and "未找到匹配" in zero.content
    assert path.read_text() == "old old"
    assert not (await execute("write_file", path=str(path), content="old\r\n")).is_error
    assert not (
        await execute("edit_file", path=str(path), old_string="old", new_string="新")
    ).is_error
    assert path.read_bytes() == "新\r\n".encode()
    assert not (await execute("write_file", path=str(path), content="")).is_error
    assert path.read_bytes() == b""


@pytest.mark.parametrize(
    "name,args",
    [
        ("read_file", "{}"),
        ("write_file", '{"path":1,"content":"x"}'),
        ("edit_file", '{"path":"x","old_string":"","new_string":""}'),
        ("bash", '{"command":null}'),
        ("glob", "[]"),
        ("grep", "{"),
        ("missing", "{}"),
        ("read_file", ""),
        ("read_file", '{"path":"\\u0000"}'),
    ],
)
async def test_invalid_arguments_are_results(name, args):
    result = await new_default_registry().execute(name, args)
    assert result.is_error and result.content


async def test_bash_output_exit_and_bounded_capture():
    result = await execute("bash", command="echo hi")
    assert not result.is_error and "exit_code: 0" in result.content
    assert "hi" in result.content and "stderr:" in result.content
    result = await execute(
        "bash",
        command=shell_python(
            "import sys; print('problem', file=sys.stderr); sys.exit(7)"
        ),
    )
    assert result.is_error and "exit_code: 7" in result.content
    assert "problem" in result.content
    result = await execute("bash", command=shell_python("print('x'*1000000)"))
    assert "[truncated]" in result.content and len(result.content) < 30000


async def test_bash_timeout_kills_child_before_side_effect(tmp_path):
    marker = tmp_path / "late.txt"
    command = shell_python(
        "import time; from pathlib import Path; time.sleep(1); "
        f"Path({str(marker)!r}).touch()"
    )
    result = await new_default_registry().execute(
        "bash", json.dumps({"command": command}), timeout=0.3
    )
    assert result.is_error and "超时" in result.content
    await asyncio.sleep(1.2)
    assert not marker.exists()


async def test_glob_grep_and_limits(tmp_path):
    (tmp_path / "nested").mkdir()
    path = tmp_path / "nested/a.py"
    path.write_text("one\nneedle\n", encoding="utf-8")
    assert (
        "nested/a.py"
        in (await execute("glob", path=str(tmp_path), pattern="**/*.py")).content
    )
    result = await execute("grep", path=str(tmp_path), pattern="needle", glob="*.py")
    assert f"{path}:2:needle" in result.content
    assert "无命中" in (await execute("grep", path=str(path), pattern="absent")).content
    assert (
        "无匹配" in (await execute("glob", path=str(tmp_path), pattern="*.go")).content
    )
    assert (await execute("grep", path=str(path), pattern="[")).is_error
    path.write_text("needle\n" * 2100, encoding="utf-8")
    assert "[truncated]" in (await execute("read_file", path=str(path))).content
    assert (
        "[truncated]"
        in (await execute("grep", path=str(path), pattern="needle")).content
    )
    path.write_text("a" * 1048576, encoding="utf-8")
    assert "[truncated]" in (await execute("read_file", path=str(path))).content
    assert "未完整搜索" in (await execute("grep", path=str(path), pattern="a")).content
    for index in range(105):
        (tmp_path / f"{index}.py").touch()
    result = await execute("glob", path=str(tmp_path), pattern="**/*.py")
    assert "[truncated]" in result.content
    expected = sorted(
        p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.py")
    )[:100]
    assert result.content.splitlines()[:-1] == expected


async def test_registry_timeout_exception_and_cancellation():
    class Slow:
        def name(self):
            return "slow"

        def description(self):
            return "测试"

        def parameters(self):
            return {}

        async def execute(self, args):
            if args == "raise":
                raise RuntimeError("broken")
            await asyncio.sleep(10)
            return Result("ok")

    registry = Registry()
    registry.register(Slow())
    assert (await registry.execute("slow", "{}", timeout=0.01)).is_error
    assert "broken" in (await registry.execute("slow", "raise")).content
    task = asyncio.create_task(registry.execute("slow", "{}"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
