"""已编译的 Hook 规则和动作参数。"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar

from termcode.hook.event import Event
from termcode.permission.matcher import Matcher

type Payload = dict[str, Any]


class CombineMode(StrEnum):
    ALL_OF = "all_of"
    ANY_OF = "any_of"


class ActionType(StrEnum):
    SHELL = "shell"
    PROMPT = "prompt"
    HTTP = "http"
    SUBAGENT = "subagent"


@dataclass(frozen=True)
class AtomCondition:
    field: str
    matcher: Matcher


@dataclass(frozen=True)
class Condition:
    mode: CombineMode
    atoms: tuple[AtomCondition, ...]


@dataclass(frozen=True)
class ShellAction:
    type: ClassVar[ActionType] = ActionType.SHELL
    command: str


@dataclass(frozen=True)
class PromptAction:
    type: ClassVar[ActionType] = ActionType.PROMPT
    text: str


@dataclass(frozen=True)
class HttpAction:
    type: ClassVar[ActionType] = ActionType.HTTP
    url: str
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None


@dataclass(frozen=True)
class SubagentAction:
    type: ClassVar[ActionType] = ActionType.SUBAGENT
    agent_name: str
    prompt: str


type Action = ShellAction | PromptAction | HttpAction | SubagentAction


@dataclass(frozen=True)
class Rule:
    name: str
    event: Event
    action: Action
    condition: Condition | None = None
    only_once: bool = False
    asyncio_mode: bool = False
    timeout_s: float = 30.0
    source: str = ""
