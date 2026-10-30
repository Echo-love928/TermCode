"""并发连接 MCP server，让各会话在同一个所属任务中进入、退出。"""

import asyncio
import os
import sys
from collections import Counter
from contextlib import AsyncExitStack
from dataclasses import dataclass

import httpx2
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Implementation

from termcode.mcp.config import Config, ServerConfig
from termcode.mcp.tool import McpTool, adapt_tool

connect_timeout: float = 30.0
close_timeout: float = 5.0


@dataclass
class _Session:
    name: str
    client: Client
    protocol_version: str


class Manager:
    def __init__(self) -> None:
        self._sessions: list[_Session] = []
        self._tools: list[McpTool] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._stop = asyncio.Event()
        self._closed = False

    def tools(self) -> list[McpTool]:
        return list(self._tools)

    async def close(self) -> None:
        self._stop.set()
        if self._closed or not self._tasks:
            return
        self._closed = True
        deadline = asyncio.get_running_loop().time() + close_timeout
        _, pending = await asyncio.wait(self._tasks, timeout=close_timeout * 0.9)
        if pending:
            print(f"[mcp] warn: close timeout ({close_timeout:g}s)", file=sys.stderr)
            for task in pending:
                task.cancel()
            # 不用 wait_for 关闭 AnyIO 上下文：它会换任务，破坏取消域归属。
            await asyncio.wait(
                pending,
                timeout=max(0, deadline - asyncio.get_running_loop().time()),
            )
        self._sessions.clear()


async def _connect_one(
    manager: Manager,
    name: str,
    server: ServerConfig,
    version: str,
    ready: asyncio.Future[None],
) -> None:
    startup = asyncio.timeout(connect_timeout)
    try:
        # 每个 server 独立退栈，失败会立即释放自己的半成品连接。
        async with AsyncExitStack() as stack:
            async with startup:
                if server.type == "stdio":
                    transport = stdio_client(
                        StdioServerParameters(
                            command=server.command,
                            args=server.args,
                            env={**os.environ, **server.env},
                        )
                    )
                else:
                    http = await stack.enter_async_context(
                        httpx2.AsyncClient(headers=server.headers)
                    )
                    transport = streamable_http_client(server.url, http_client=http)
                client = await stack.enter_async_context(
                    Client(
                        transport,
                        mode="auto",
                        client_info=Implementation(name="termcode", version=version),
                        cache=None,
                        input_required_max_rounds=0,
                    )
                )
                tools: list[McpTool] = []
                cursor = None
                seen_cursors: set[str] = set()
                seen_names: set[str] = set()
                while True:
                    listed = await client.list_tools(cursor=cursor)
                    for remote in listed.tools:
                        tool = adapt_tool(
                            name,
                            remote,
                            client,
                            secrets=tuple(server.env.values())
                            + tuple(server.headers.values()),
                        )
                        if tool is not None and tool.name() not in seen_names:
                            tools.append(tool)
                            seen_names.add(tool.name())
                    cursor = listed.next_cursor
                    if cursor is None:
                        break
                    if cursor in seen_cursors:
                        raise ValueError("tools/list 分页游标重复")
                    seen_cursors.add(cursor)
                protocol = client.session.protocol_version or "unknown"
                if ready.cancelled():
                    return
                manager._sessions.append(_Session(name, client, protocol))
                manager._tools.extend(tools)
                print(
                    f"[mcp] connected server {name!r}: "
                    f"protocol={protocol}, tools={len(tools)}",
                    file=sys.stderr,
                )
            if not ready.done():
                ready.set_result(None)
            await manager._stop.wait()
    except TimeoutError:
        print(
            f"[mcp] warn: connect server {name!r} timeout after {connect_timeout:g}s",
            file=sys.stderr,
        )
    except Exception as exc:
        # 传输异常可能包含 URL、Authorization 或环境值，只输出异常类型。
        reason = (
            f"timeout after {connect_timeout:g}s"
            if startup.expired()
            else f"failed ({type(exc).__name__})"
        )
        print(f"[mcp] warn: server {name!r} {reason}", file=sys.stderr)
    finally:
        if not ready.done():
            ready.set_result(None)


async def new_manager(config: Config, version: str) -> Manager:
    manager = Manager()
    ready = []
    for name, server in config.servers.items():
        signal = asyncio.get_running_loop().create_future()
        ready.append(signal)
        manager._tasks.append(
            asyncio.create_task(
                _connect_one(manager, name, server, version, signal),
                name=f"mcp:{name}",
            )
        )
    try:
        if ready:
            _, pending = await asyncio.wait(ready, timeout=connect_timeout)
            for name, signal in zip(config.servers, ready, strict=True):
                if signal in pending:
                    # 退栈可能等待子进程退出，不能让这段等待拖延进入 TUI。
                    signal.cancel()
                    print(
                        f"[mcp] warn: connect server {name!r} "
                        f"timeout after {connect_timeout:g}s",
                        file=sys.stderr,
                    )
    except BaseException:
        for task in manager._tasks:
            task.cancel()
        await manager.close()
        raise
    counts = Counter(tool.name() for tool in manager._tools)
    for name, count in counts.items():
        if count > 1:
            print(f"[mcp] warn: skip ambiguous tool name {name!r}", file=sys.stderr)
    manager._tools = sorted(
        (tool for tool in manager._tools if counts[tool.name()] == 1),
        key=lambda tool: tool.name(),
    )
    return manager
