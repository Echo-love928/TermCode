"""真实 MCP 子进程、HTTP、生命周期与两种 LLM 协议的集成验收。"""

import asyncio
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from mcp_server_fixture import response
from test_providers import config as provider_config
from test_tool_protocols import call

from termcode.agent import Agent
from termcode.conversation import Conversation
from termcode.llm import ToolCall, new_provider
from termcode.mcp import Config, ServerConfig, new_manager
from termcode.mcp import manager as module
from termcode.permission import Mode, new_engine
from termcode.permission.rule import parse_rule
from termcode.permission.settings import extract_target, load_settings, to_rule_set
from termcode.permission.types import Decision
from termcode.tool import new_default_registry

FIXTURE = Path(__file__).with_name("mcp_server_fixture.py")


def stdio(*args, **env):
    return ServerConfig(
        "stdio", command=sys.executable, args=["-u", str(FIXTURE), *args], env=env
    )


def process_exists(pid):
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        status = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(status))
        ctypes.windll.kernel32.CloseHandle(handle)
        return status.value == 259
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@pytest.mark.parametrize("legacy", [False, True])
async def test_real_stdio_handshake_env_and_exit(tmp_path, legacy):
    trace = tmp_path / "trace.jsonl"
    pid_file = tmp_path / "pid"
    manager = await new_manager(
        Config(
            {
                "demo": stdio(
                    *(["--legacy"] if legacy else []),
                    MCP_TEST_TRACE=str(trace),
                    MCP_TEST_PID=str(pid_file),
                    MCP_TEST_VALUE="expanded-value",
                )
            }
        ),
        "test-version",
    )
    pid = int(pid_file.read_text())
    try:
        assert len(manager.tools()) == 4
        assert process_exists(pid)
        tools = {tool.name(): tool for tool in manager.tools()}
        assert tools["mcp__demo__read_echo"].read_only
        result = await tools["mcp__demo__echo"].execute('{"text":"ENV_PROBE"}')
        assert not result.is_error and result.content == "expanded-value"
        result = await tools["mcp__demo__fail"].execute('{"text":"remote failure"}')
        assert result.is_error and result.content == "remote failure"
        result = await tools["mcp__demo__protocol_error"].execute('{"text":"x"}')
        assert result.is_error and "fixture error" in result.content
        version = manager._sessions[0].protocol_version
        assert version == ("2025-11-25" if legacy else "2026-07-28")
        messages = [json.loads(line) for line in trace.read_text().splitlines()]
        assert messages[0]["method"] == "server/discover"
        methods = [message["method"] for message in messages]
        assert ("initialize" in methods) is legacy
        if legacy:
            assert "notifications/initialized" in methods
            info = next(m for m in messages if m["method"] == "initialize")["params"][
                "clientInfo"
            ]
            assert info == {"name": "termcode", "version": "test-version"}
    finally:
        await manager.close()
    assert not process_exists(pid)
    assert all(task.done() for task in manager._tasks)
    await manager.close()


async def test_empty_manager_and_parallel_failure_isolation(tmp_path, capsys):
    empty = await new_manager(Config(), "test")
    assert not empty.tools()
    await empty.close()
    manager = await new_manager(
        Config(
            {
                "z": stdio(),
                "a": stdio(),
                "broken": ServerConfig("stdio", command=str(tmp_path / "no-command")),
            }
        ),
        "test",
    )
    try:
        names = [tool.name() for tool in manager.tools()]
        assert len(names) == 8 and names == sorted(names)
        copy = manager.tools()
        copy.clear()
        assert len(manager.tools()) == 8
        assert "broken" in capsys.readouterr().err
    finally:
        await manager.close()
    assert all(task.done() for task in manager._tasks)


async def test_discover_no_response_falls_back_on_same_process(tmp_path):
    trace = tmp_path / "trace.jsonl"
    manager = await new_manager(
        Config({"old": stdio("--ignore-discover", MCP_TEST_TRACE=str(trace))}),
        "test",
    )
    try:
        assert len(manager.tools()) == 4
        assert manager._sessions[0].protocol_version == "2025-11-25"
        messages = [json.loads(line) for line in trace.read_text().splitlines()]
        assert [m["method"] for m in messages][:3] == [
            "server/discover",
            "initialize",
            "notifications/initialized",
        ]
    finally:
        await manager.close()


async def test_all_tool_pages_are_registered(tmp_path):
    trace = tmp_path / "trace.jsonl"
    manager = await new_manager(
        Config({"paged": stdio("--paginate", MCP_TEST_TRACE=str(trace))}), "test"
    )
    try:
        assert len(manager.tools()) == 4
        messages = [json.loads(line) for line in trace.read_text().splitlines()]
        pages = [m for m in messages if m["method"] == "tools/list"]
        assert len(pages) == 2
        assert pages[1]["params"]["cursor"] == "second-page"
    finally:
        await manager.close()


async def test_ambiguous_namespace_does_not_break_startup(monkeypatch, capsys):
    import mcp.types as types

    from termcode.mcp.tool import adapt_tool

    async def connect(manager, name, server, version, ready):
        remote = "b__c" if name == "a" else "c"
        tool = adapt_tool(name, types.Tool(name=remote, input_schema={}), None)
        manager._tools.append(tool)
        ready.set_result(None)
        await manager._stop.wait()

    monkeypatch.setattr(module, "_connect_one", connect)
    manager = await new_manager(
        Config({name: ServerConfig("stdio") for name in ("a", "a__b")}), "test"
    )
    try:
        assert not manager.tools()
        assert "ambiguous tool name" in capsys.readouterr().err
    finally:
        await manager.close()


async def test_startup_timeout_closes_partial_connection(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(module, "connect_timeout", 0.8)
    pid_file = tmp_path / "pid"
    start = time.monotonic()
    manager = await new_manager(
        Config({"stuck": stdio("--stall-list", MCP_TEST_PID=str(pid_file))}), "test"
    )
    elapsed = time.monotonic() - start
    try:
        assert not manager.tools()
        assert elapsed < 1.5
        assert "timeout" in capsys.readouterr().err
    finally:
        await manager.close()
    assert not process_exists(int(pid_file.read_text()))


async def test_cancelling_startup_cleans_process(tmp_path):
    pid_file = tmp_path / "pid"
    task = asyncio.create_task(
        new_manager(
            Config({"stuck": stdio("--stall-list", MCP_TEST_PID=str(pid_file))}), "test"
        )
    )
    async with asyncio.timeout(5):
        while not pid_file.exists():
            await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not process_exists(int(pid_file.read_text()))


async def test_close_timeout_cancels_owner_task(monkeypatch, capsys):
    monkeypatch.setattr(module, "close_timeout", 0.03)
    manager = module.Manager()

    async def stuck():
        await asyncio.Event().wait()

    task = asyncio.create_task(stuck())
    manager._tasks.append(task)
    start = time.monotonic()
    await manager.close()
    assert time.monotonic() - start < 0.2
    assert task.done() and task.cancelled()
    assert "close timeout" in capsys.readouterr().err


@pytest.fixture
def http_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.server.requests.append((message, dict(self.headers)))
            if (
                self.server.reject_discover
                and message.get("method") == "server/discover"
            ):
                self.send_response(404)
                self.end_headers()
                return
            result = response(message, legacy=self.server.legacy)
            if result is None:
                self.send_response(202)
                self.end_headers()
                return
            data = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.requests = []
    server.legacy = False
    server.reject_discover = False
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("legacy,reject", [(False, False), (True, False), (True, True)])
async def test_http_headers_protocol_and_close(http_server, legacy, reject):
    http_server.legacy = legacy
    http_server.reject_discover = reject
    url = f"http://127.0.0.1:{http_server.server_port}/mcp"
    manager = await new_manager(
        Config(
            {
                "http": ServerConfig(
                    "http", url=url, headers={"Authorization": "Bearer fixture-value"}
                )
            }
        ),
        "test",
    )
    try:
        assert len(manager.tools()) == 4
        result = await manager.tools()[0].execute('{"text":"http echo"}')
        assert result.content == "http echo" and not result.is_error
        for message, headers in http_server.requests:
            headers = {key.lower(): value for key, value in headers.items()}
            assert headers["authorization"] == "Bearer fixture-value"
            if not legacy:
                assert headers["mcp-protocol-version"] == "2026-07-28"
                assert headers["mcp-method"] == message["method"]
                if message["method"] == "tools/call":
                    assert headers["mcp-name"] == "echo"
    finally:
        await manager.close()
    assert all(task.done() for task in manager._tasks)


def test_mcp_permission_exact_wildcard_persist_and_unknown_safety(tmp_path):
    engine, _ = new_engine(str(tmp_path))
    name = "mcp__my-server__echo"
    tool_call = ToolCall("c1", name, '{"path":"/outside", "command":"rm -rf /"}')
    assert engine.check(Mode.BYPASS, tool_call)[0] == Decision.DENY
    engine.mcp_tools = frozenset({name})
    assert extract_target(tool_call) == ("", False, False)
    assert engine.check(Mode.DEFAULT, tool_call)[0] == Decision.ASK
    assert engine.check(Mode.DEFAULT, tool_call, True)[0] == Decision.ALLOW
    assert engine.check(Mode.BYPASS, tool_call)[0] == Decision.ALLOW
    rule, valid = parse_rule("mcp__my-server__*")
    assert valid
    engine.project.allow.append(rule)
    assert engine.check(Mode.DEFAULT, tool_call)[0] == Decision.ALLOW
    engine.project.deny.append(rule)
    assert engine.check(Mode.BYPASS, tool_call)[0] == Decision.DENY
    engine.project.deny.clear()
    engine.persist_local_allow(tool_call)
    settings = load_settings(engine.local_path)
    assert settings.permissions.allow == [name]
    assert to_rule_set(settings).match(name, "") == (Decision.ALLOW, True)
    reloaded, _ = new_engine(str(tmp_path))
    reloaded.mcp_tools = engine.mcp_tools
    assert reloaded.check(Mode.DEFAULT, tool_call)[0] == Decision.ALLOW


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_mcp_agent_real_providers_feedback_and_error_recovery(
    sse_server, protocol, tmp_path
):
    manager = await new_manager(Config({"demo": stdio()}), "test")
    registry = new_default_registry()
    for tool in manager.tools():
        registry.register(tool)
    engine, _ = new_engine(str(tmp_path))
    engine.mcp_tools = frozenset(tool.name() for tool in manager.tools())
    sse_server.responses = [
        {"calls": [call("mcp__demo__fail", text="fixture-error")]},
        {"calls": [call("mcp__demo__read_echo", text="recovered")]},
        {"text": "MCP_DONE"},
    ]
    provider = new_provider(provider_config(sse_server, protocol))
    conversation = Conversation()
    conversation.add_user("调用 MCP 工具，失败后换一个只读工具再总结")
    try:
        events = [
            event
            async for event in Agent(provider, registry, engine=engine).run(
                conversation, Mode.BYPASS
            )
        ]
        assert events[-1].done and not any(event.err for event in events)
        messages = conversation.messages()
        assert messages[2].tool_results[0].is_error
        assert messages[4].tool_results[0].content == "recovered"
        assert len(sse_server.requests[0]["body"]["tools"]) == 10
    finally:
        await provider.aclose()
        await manager.close()
