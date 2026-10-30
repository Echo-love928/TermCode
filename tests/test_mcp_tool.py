"""远端工具命名、参数、返回内容、错误与取消。"""

import asyncio
from types import SimpleNamespace

import mcp.types as t
import pytest

from termcode.mcp import tool as module
from termcode.mcp.tool import adapt_tool
from termcode.tool import Tool, new_default_registry


class Stub:
    def __init__(self, result=None, error=None, hang=False):
        self.result = result or t.CallToolResult(content=[])
        self.error = error
        self.hang = hang
        self.calls = []

    async def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments))
        if self.hang:
            await asyncio.Event().wait()
        if self.error:
            raise self.error
        return self.result


def remote(name="echo", **kwargs):
    return t.Tool(name=name, input_schema={}, **kwargs)


@pytest.mark.parametrize(
    "server,name", [("a.b", "echo"), ("a", "e@cho"), ("a", "汉字")]
)
def test_invalid_names(server, name, capsys):
    assert adapt_tool(server, remote(name), Stub()) is None
    assert "illegal characters" in capsys.readouterr().err


@pytest.mark.parametrize(
    "hint,expected",
    [(None, False), (False, False), (True, True), (1, False), ("true", False)],
)
def test_fields_and_safe_readonly(hint, expected):
    annotations = SimpleNamespace(read_only_hint=hint) if hint is not None else None
    value = SimpleNamespace(
        name="echo", description=None, input_schema=None, annotations=annotations
    )
    tool = adapt_tool("demo", value, Stub())
    assert isinstance(tool, Tool)
    assert tool.name() == "mcp__demo__echo" and tool.description()
    assert tool.parameters() == {"type": "object"} and tool.read_only is expected


async def test_registry_namespace_schema_arguments_and_text():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    caller = Stub(
        t.CallToolResult(
            content=[
                t.TextContent(type="text", text="one"),
                t.TextContent(type="text", text="two"),
            ]
        )
    )
    registry = new_default_registry()
    for server in ("a", "b"):
        registry.register(
            adapt_tool(server, t.Tool(name="echo", input_schema=schema), caller)
        )
    assert len(registry.definitions()) == 8
    assert registry.get("mcp__b__echo").parameters() == schema
    result = await registry.execute("mcp__a__echo", '{"n": 3, "nested": [true]}')
    assert result.content == "one\ntwo" and not result.is_error
    assert caller.calls == [("echo", {"n": 3, "nested": [True]})]
    await registry.execute("mcp__b__echo", "{}")
    assert caller.calls[-1] == ("echo", None)


@pytest.mark.parametrize("args", ["[]", "null", "42", "not json"])
async def test_bad_arguments_never_call_server(args):
    caller = Stub()
    assert (await adapt_tool("a", remote(), caller).execute(args)).is_error
    assert not caller.calls


async def test_non_text_warns_once_and_remote_error(capsys):
    content = [
        t.TextContent(type="text", text="before"),
        t.ImageContent(type="image", data="", mime_type="image/png"),
        t.AudioContent(type="audio", data="", mime_type="audio/wav"),
        t.ResourceLink(type="resource_link", name="x", uri="https://example.com"),
        t.EmbeddedResource(
            type="resource",
            resource=t.TextResourceContents(uri="file:///x", text="ignored"),
        ),
        t.TextContent(type="text", text="after"),
    ]
    tool = adapt_tool(
        "a", remote(), Stub(t.CallToolResult(content=content, is_error=True))
    )
    for _ in range(2):
        result = await tool.execute("{}")
        assert result.content == "before\nafter" and result.is_error
    assert capsys.readouterr().err.count("non-text content") == 1


async def test_exception_timeout_redaction_and_cancel(monkeypatch):
    tool = adapt_tool(
        "a",
        remote(),
        Stub(error=ValueError("private-value bad request")),
        secrets=("private-value",),
    )
    result = await tool.execute("{}")
    assert (
        result.is_error
        and "bad request" in result.content
        and "private-value" not in result.content
    )
    monkeypatch.setattr(module, "call_timeout", 0.02)
    tool = adapt_tool("a", remote(), Stub(hang=True))
    assert "超时" in (await tool.execute("{}")).content
    monkeypatch.setattr(module, "call_timeout", 10)
    task = asyncio.create_task(tool.execute("{}"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
