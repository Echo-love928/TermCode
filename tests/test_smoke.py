"""验证 smoke 可打印缓存用量，并对跨片段密钥整体脱敏。"""

import argparse
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from termcode.agent import Event, Usage
from termcode.llm import StreamEvent
from termcode.llm import Usage as LLMUsage

SMOKE_PATH = Path(__file__).resolve().parents[1] / "examples/smoke.py"


async def test_smoke_cache_output_and_fragmented_secret(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("termcode_smoke_test", SMOKE_PATH)
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    closed = False

    class Provider:
        async def aclose(self):
            nonlocal closed
            closed = True

    class Agent:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, *args):
            yield Event(text="TEST_")
            yield Event(text="SECRET")
            yield Event(usage=Usage(10, 5, 100, 80), done=True)

    cfg = SimpleNamespace(
        name="test", api_key="TEST_SECRET", protocol="openai", context_window=0
    )
    monkeypatch.setattr(smoke, "load", lambda p: SimpleNamespace(providers=[cfg]))
    monkeypatch.setattr(smoke, "new_provider", lambda c: Provider())
    monkeypatch.setattr(smoke, "Agent", Agent)
    args = argparse.Namespace(
        config="unused", provider="test", plan=False, messages=["测试"]
    )
    await smoke.run(args)
    output = capsys.readouterr().out
    assert "TEST_" not in output and "SECRET" not in output
    assert "[已隐藏密钥]" in output
    assert '"cache_write": 100' in output and '"cache_read": 80' in output
    assert closed


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_smoke_explicit_ask_in_bypass_denied_without_blocking(
    sse_server, protocol, tmp_path, monkeypatch, capsys
):
    from test_tool_protocols import call
    from test_tui import local_config

    monkeypatch.chdir(tmp_path)
    directory = tmp_path / ".termcode"
    directory.mkdir()
    (directory / "settings.yaml").write_text(
        "permissions:\n  ask: ['Write']\n", encoding="utf-8"
    )
    sse_server.responses = [
        {"calls": [call("write_file", path="never.txt", content="never")]},
        {"text": "工具被拒后继续回复"},
    ]
    spec = importlib.util.spec_from_file_location("smoke_ask_test", SMOKE_PATH)
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    cfg = local_config(sse_server, protocol)
    monkeypatch.setattr(smoke, "load", lambda path: SimpleNamespace(providers=[cfg]))
    args = argparse.Namespace(
        config="unused", provider=None, plan=False, messages=["请创建文件"]
    )
    assert await smoke.run(args) == 0
    output = capsys.readouterr().out
    assert "非交互模式无法确认" in output and "工具被拒后继续回复" in output
    assert not (tmp_path / "never.txt").is_file()
    assert "TEST_SECRET" not in output
    assert len(sse_server.requests) == 2


@pytest.mark.parametrize(
    ("write", "reported", "read", "status", "exit_code"),
    [
        (2048, True, 2048, "write_read_verified", 0),
        (0, False, 2048, "read_verified_write_unreported", 2),
        (0, True, 2048, "read_verified_write_not_observed", 2),
        (0, False, 0, "cache_not_observed", 1),
    ],
)
async def test_cold_cache_check_distinguishes_missing_write(
    write,
    reported,
    read,
    status,
    exit_code,
):
    spec = importlib.util.spec_from_file_location(
        "termcode_cache_smoke_test", SMOKE_PATH
    )
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)

    class Provider:
        def __init__(self):
            self.requests = []

        async def stream(self, req):
            self.requests.append(req)
            usage = (
                LLMUsage(10, 2, write, 0, cache_write_reported=reported)
                if len(self.requests) == 1
                else LLMUsage(10, 2, 0, read)
            )
            yield StreamEvent(usage=usage, text="OK", done=True)

    provider, output = Provider(), []
    assert await smoke.check_cache(provider, output.append) == exit_code
    summary = __import__("json").loads(output[-1])["cache_check"]
    assert summary["status"] == status
    assert summary["native_ac4_passed"] == (exit_code == 0)
    first, second = provider.requests
    assert first.system.stable == second.system.stable
    assert first.system.environment == second.system.environment == ""
    assert not first.tools and not second.tools
    assert "TermCode" not in first.system.stable
    assert len(first.system.stable) > 20000
    assert [m.role for m in second.messages] == ["user", "assistant", "user"]
    other = Provider()
    await smoke.check_cache(other, lambda value: None)
    assert other.requests[0].system.stable != first.system.stable
