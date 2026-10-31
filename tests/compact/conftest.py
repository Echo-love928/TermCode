"""摘要测试统一使用无网络、可脚本化的供应商。"""

import pytest

from termcode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    ManageInput,
    RecoveryState,
    TriggerKind,
    new_session_context,
)
from termcode.compact.summary_prompt import SUMMARY_TITLES
from termcode.conversation import Conversation
from termcode.llm import StreamEvent, Usage

SUMMARY = (
    "<analysis>草稿</analysis><summary>" + "\n".join(SUMMARY_TITLES) + "</summary>"
)


class FakeCompactProvider:
    name = "fake"
    model = "test-model"

    def __init__(self, responses=None):
        self.responses = responses or [[StreamEvent(text=SUMMARY)]]
        self.requests = []

    async def stream(self, req):
        self.requests.append(req)
        events = self.responses.pop(0)
        for event in events:
            yield event
        yield StreamEvent(usage=Usage(100, 50, 20, 30))
        yield StreamEvent(done=True)

    async def aclose(self):
        pass


@pytest.fixture
def fake_compact_provider():
    return FakeCompactProvider


@pytest.fixture
def make_input(tmp_path):
    def make(msgs=(), **kwargs):
        conv = Conversation()
        conv.replace_history(list(msgs))
        return ManageInput(
            **{
                "conv": conv,
                "provider": FakeCompactProvider(),
                "model": "test-model",
                "context_window": 200000,
                "tool_defs": [],
                "replacement": ContentReplacementState(),
                "recovery": RecoveryState(),
                "auto_tracking": CompactCircuitBreaker(),
                "session": new_session_context(str(tmp_path)),
                "usage_anchor": 0,
                "anchor_msg_len": 0,
                "estimated_token": 0,
                "trigger": TriggerKind.AUTO,
                **kwargs,
            }
        )

    return make
