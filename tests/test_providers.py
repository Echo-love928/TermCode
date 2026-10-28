"""通过真实 HTTP 和 SDK 检验协议，服务端为本地 SSE 测试替身。"""

import asyncio

import pytest

from termcode.config import ProviderConfig
from termcode.llm import Message, new_provider
from termcode.prompt import SYSTEM_PROMPT


def config(server, protocol, **changes):
    return ProviderConfig(
        name="local",
        protocol=protocol,
        api_key="TEST_SECRET",
        model="test-model",
        base_url=server.url + ("/v1" if protocol == "openai" else ""),
        **changes,
    )


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
async def test_real_sdk_sse_history_thinking_and_headers(sse_server, protocol):
    provider = new_provider(config(sse_server, protocol, thinking=True))
    messages = [
        Message("user", "记住名字小林"),
        Message("assistant", "好的"),
        Message("user", "我的名字？"),
    ]
    try:
        events = [event async for event in provider.stream(messages)]
    finally:
        await provider.aclose()
    assert events[-1].done
    assert all(event.err is None for event in events)
    text = "".join(event.text for event in events)
    assert "**你好**" in text and "```python" in text
    assert "HIDDEN_THINKING" not in text
    request = sse_server.requests[0]
    body = request["body"]
    assert body["stream"] is True
    assert body["model"] == "test-model"
    assert "tools" not in body
    if protocol == "anthropic":
        assert body["system"] == SYSTEM_PROMPT
        assert body["thinking"] == {"type": "enabled", "budget_tokens": 2048}
        assert body["max_tokens"] == 4096
        headers = request["headers"]
        assert (
            headers.get("x-api-key") == "TEST_SECRET"
            or headers.get("authorization") == "Bearer TEST_SECRET"
        )
        assert request["path"] == "/v1/messages"
        assert body["messages"][0]["content"] == messages[0].content
    else:
        assert body["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
        assert "thinking" not in body
        assert request["headers"]["authorization"] == "Bearer TEST_SECRET"
        assert request["path"] == "/v1/chat/completions"
    assert body["messages"][-1]["content"] == "我的名字？"


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
async def test_auth_error_is_event_and_not_retried(sse_server, protocol):
    sse_server.mode = "auth_error"
    provider = new_provider(config(sse_server, protocol))
    try:
        events = [event async for event in provider.stream([Message("user", "你好")])]
    finally:
        await provider.aclose()
    assert len(events) == 1
    assert getattr(events[0].err, "status_code", None) == 401
    assert not events[0].done
    assert len(sse_server.requests) == 1


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
async def test_truncated_stream_is_not_success(sse_server, protocol):
    sse_server.mode = "truncated"
    provider = new_provider(config(sse_server, protocol))
    try:
        events = [event async for event in provider.stream([Message("user", "你好")])]
    finally:
        await provider.aclose()
    assert any(event.text for event in events)
    assert events[-1].err is not None
    assert not any(event.done for event in events)


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
async def test_cancellation_propagates(sse_server, protocol):
    sse_server.gate.clear()
    provider = new_provider(config(sse_server, protocol))

    async def consume():
        return [event async for event in provider.stream([Message("user", "你好")])]

    task = asyncio.create_task(consume())
    try:
        for _ in range(100):
            if sse_server.requests:
                break
            await asyncio.sleep(0.01)
        assert sse_server.requests
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        sse_server.gate.set()
        await provider.aclose()
