"""与具体界面无关的命令元数据。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum

from .ui import UI


class Kind(Enum):
    LOCAL = "local"
    UI = "ui"
    PROMPT = "prompt"


Handler = Callable[[UI], Awaitable[None]]


@dataclass(slots=True)
class Command:
    name: str
    description: str
    kind: Kind
    handler: Handler
    aliases: list[str] = field(default_factory=list)
    hidden: bool = False
