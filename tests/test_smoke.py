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
        def __init__(self, *args):
            pass

        async def run(self, *args):
            yield Event(text="TEST_")
            yield Event(text="SECRET")
            yield Event(usage=Usage(10, 5, 100, 80), done=True)

    cfg = SimpleNamespace(name="test", api_key="TEST_SECRET")
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
