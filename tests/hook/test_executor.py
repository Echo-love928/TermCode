"""验证真实 shell 子进程、HTTP 动作和占位动作。"""

import asyncio
import json
import os
import shlex
import subprocess
import sys

import httpx2
import pytest

from termcode.hook import Event
from termcode.hook.executor import Executor
from termcode.hook.rule import (
    HttpAction,
    PromptAction,
    Rule,
    ShellAction,
    SubagentAction,
)


def shell_python(script):
    args = [sys.executable, "-c", script]
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)


@pytest.mark.parametrize(
    ("code", "blocking", "blocked", "error"),
    [
        (0, True, False, False),
        (1, True, False, True),
        (2, False, False, True),
        (2, True, True, False),
    ],
)
async def test_run_shell_exit(code, blocking, blocked, error, capsys):
    command = shell_python(
        f"import sys; print('hook reason', file=sys.stderr); sys.exit({code})"
    )
    rule = Rule("shell", Event.PRE_TOOL_USE, ShellAction(command))
    result = await Executor().run(rule, {}, blocking=blocking)
    assert result.blocked is blocked
    assert (result.err is not None) is error
    if blocked:
        assert result.reason == "hook reason"
    if code == 0:
        assert capsys.readouterr().err.splitlines() == ["hook reason"]


async def test_run_shell_payload(tmp_path):
    command = shell_python(
        "from pathlib import Path; import sys; "
        "Path('payload.txt').write_bytes(sys.stdin.buffer.readline())"
    )
    rule = Rule("shell", Event.STOP, ShellAction(command))
    payload = {"z": 1, "cwd": str(tmp_path), "a": 2}
    assert (await Executor().run(rule, payload, blocking=False)).err is None
    raw = (tmp_path / "payload.txt").read_bytes()
    assert raw == json.dumps(payload, sort_keys=True).encode() + b"\n"


async def test_run_shell_timeout_and_cancel():
    command = shell_python("import time; time.sleep(20)")
    rule = Rule("shell", Event.STOP, ShellAction(command), timeout_s=0.1)
    executor = Executor()
    async with asyncio.timeout(3):
        result = await executor.run(rule, {}, blocking=False)
        assert isinstance(result.err, TimeoutError)
        task = asyncio.create_task(
            executor.run(
                Rule("cancel", Event.STOP, ShellAction(command)), {}, blocking=False
            )
        )
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_shell_cancel_during_spawn_reaps_process(monkeypatch):
    original = asyncio.create_subprocess_shell
    started, release = asyncio.Event(), asyncio.Event()
    processes = []

    async def spawn(*args, **kwargs):
        started.set()
        await release.wait()
        proc = await original(*args, **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_shell", spawn)
    command = shell_python("import time; time.sleep(20)")
    rule = Rule("shell", Event.STOP, ShellAction(command))
    async with asyncio.timeout(3):
        task = asyncio.create_task(Executor().run(rule, {}, blocking=False))
        await started.wait()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(processes) == 1 and processes[0].returncode is not None


@pytest.mark.parametrize(
    ("status", "body", "blocked", "error"),
    [
        (200, '{"decision":"block","reason":"policy"}', True, False),
        (200, '{"decision":"allow"}', False, False),
        (200, '{"decision":"block"}', False, False),
        (200, "invalid JSON", False, True),
        (503, '{"decision":"block","reason":"policy"}', False, True),
    ],
)
async def test_run_http_response(status, body, blocked, error):
    seen = []

    def respond(request):
        seen.append(
            (request.method, request.content, request.headers.get("Content-Type"))
        )
        return httpx2.Response(status, text=body)

    executor = Executor()
    executor._http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
    try:
        rule = Rule("policy", Event.PRE_TOOL_USE, HttpAction("http://127.0.0.1/check"))
        result = await executor.run(rule, {"b": 2, "a": 1}, blocking=True)
        assert seen == [("POST", b'{"a": 1, "b": 2}', "application/json")]
        assert result.blocked is blocked
        assert (result.err is not None) is error
        if blocked:
            assert result.reason == "policy"
    finally:
        await executor.aclose()


async def test_run_http_template_and_total_timeout():
    seen = []

    async def respond(request):
        seen.append((request.method, request.content, request.headers["X-Hook"]))
        await asyncio.sleep(20)
        return httpx2.Response(200)

    executor = Executor()
    executor._http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
    try:
        rule = Rule(
            "slow",
            Event.STOP,
            HttpAction("http://127.0.0.1/done", "PUT", {"X-Hook": "yes"}, "{event}"),
            timeout_s=0.05,
        )
        result = await executor.run(rule, {"event": "Stop"}, blocking=False)
        assert seen == [("PUT", b"Stop", "yes")]
        assert isinstance(result.err, TimeoutError)
        result = await executor.run(rule, {}, blocking=False)
        assert isinstance(result.err, KeyError) and len(seen) == 1
    finally:
        await executor.aclose()


async def test_run_prompt_and_subagent(capsys):
    executor = Executor()
    rule = Rule("reminder", Event.PRE_TOOL_USE, PromptAction("中文回复"))
    result = await executor.run(rule, {}, blocking=True)
    assert result.prompt == "中文回复" and not result.blocked
    rule = Rule("later", Event.SESSION_START, SubagentAction("helper", "test"))
    result = await executor.run(rule, {}, blocking=False)
    assert not result.blocked and result.err is None
    assert (
        capsys.readouterr().err
        == "[hook subagent] not yet implemented, skipped: helper\n"
    )
