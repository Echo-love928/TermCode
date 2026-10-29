"""检查真实 SDK 序列化后的缓存边界、消息合法性与缓存用量。"""

import copy

import pytest
from test_providers import config

from termcode.llm import Message, Request, System, ToolCall, ToolResult, new_provider
from termcode.prompt import build_system_prompt, plan_reminder
from termcode.tool import new_default_registry


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
@pytest.mark.parametrize("tail", ["user", "tool", "assistant", "empty"])
async def test_system_boundary_reminder_and_history_untouched(
    sse_server, protocol, tail
):
    messages = [Message("user", "调查")]
    if tail == "tool":
        messages += [
            Message(
                "assistant", tool_calls=[ToolCall("c1", "read_file", '{"path":"x"}')]
            ),
            Message("tool", tool_results=[ToolResult("c1", "内容")]),
        ]
    elif tail == "assistant":
        messages.append(Message("assistant", "继续调查"))
    elif tail == "empty":
        messages = []
    snapshot = copy.deepcopy(messages)
    stable, reminder = build_system_prompt(), plan_reminder(True)
    definitions = new_default_registry().read_only_definitions()
    provider = new_provider(config(sse_server, protocol))
    try:
        for environment in ("环境信息：A", "环境信息：B"):
            req = Request(messages, definitions, System(stable, environment), reminder)
            events = [e async for e in provider.stream(req)]
            assert events[-1].done and not any(e.err for e in events)
            assert req.messages == snapshot
    finally:
        await provider.aclose()
    first, second = [r["body"] for r in sse_server.requests]
    assert first["tools"] == second["tools"]
    if protocol == "anthropic":
        assert first["system"] == [
            {"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "环境信息：A"},
        ]
        assert first["system"][0] == second["system"][0]
        assert first["system"][1] != second["system"][1]
        assert all("cache_control" not in d for d in first["tools"])
        history = first["messages"]
        assert all(
            a["role"] != b["role"] for a, b in zip(history, history[1:], strict=False)
        )
        assert history[-1]["role"] == "user"
        assert history[-1]["content"][-1] == {"type": "text", "text": reminder}
        if tail == "tool":
            assert history[-1]["content"][0]["tool_use_id"] == "c1"
            assert len(history) == 3
    else:
        assert first["messages"][0] == {
            "role": "system",
            "content": stable + "\n\n环境信息：A",
        }
        assert first["messages"][-1] == {"role": "user", "content": reminder}
        if tail == "tool":
            assert first["messages"][-2]["role"] == "tool"
            assert first["messages"][-2]["tool_call_id"] == "c1"


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
@pytest.mark.parametrize("fields", ["present", "missing", "null", "empty"])
async def test_cache_usage_parsing(sse_server, protocol, fields):
    if protocol == "anthropic":
        cache = {
            "cache_creation_input_tokens": 0 if fields == "empty" else 1400,
            "cache_read_input_tokens": 0 if fields == "empty" else 1300,
        }
        if fields == "null":
            cache = {key: None for key in cache}
    else:
        cache = {
            "prompt_tokens_details": {"cached_tokens": 0 if fields == "empty" else 1300}
        }
        if fields == "null":
            cache = {"prompt_tokens_details": None}
    sse_server.responses = [
        {"text": "完成", "cache_fields": {} if fields == "missing" else cache}
    ]
    provider = new_provider(config(sse_server, protocol))
    try:
        events = [e async for e in provider.stream(Request([Message("user", "测试")]))]
    finally:
        await provider.aclose()
    assert events[-1].done and not any(e.err for e in events)
    usage = next(e.usage for e in events if e.usage)
    assert usage.cache_read == (1300 if fields == "present" else 0)
    assert usage.cache_write == (
        1400 if fields == "present" and protocol == "anthropic" else 0
    )
    assert usage.cache_write_reported == (
        protocol == "anthropic" and fields in {"present", "empty"}
    )


@pytest.mark.parametrize("write", [None, 0, 2048])
async def test_cache_fields_in_final_delta(sse_server, write):
    fields = {"cache_read_input_tokens": 2048}
    if write is not None:
        fields["cache_creation_input_tokens"] = write
    sse_server.responses = [{"text": "OK", "final_cache_fields": fields}]
    provider = new_provider(config(sse_server, "anthropic"))
    try:
        events = [e async for e in provider.stream(Request([Message("user", "测试")]))]
    finally:
        await provider.aclose()
    usage = next(e.usage for e in events if e.usage)
    assert events[-1].done and not any(e.err for e in events)
    assert usage.cache_read == 2048 and usage.cache_write == (write or 0)
    assert usage.cache_write_reported == (write is not None)
