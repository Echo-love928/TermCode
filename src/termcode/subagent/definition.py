from dataclasses import dataclass, field
from enum import IntEnum

from termcode.permission import Mode

MODELS = ("haiku", "sonnet", "opus", "inherit")


class Source(IntEnum):
    BUILTIN = 0
    USER = 1
    PROJECT = 2
    PLUGIN = 3

    def __str__(self) -> str:
        return self.name.lower()


@dataclass
class Definition:
    name: str
    description: str
    tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    model: str = "inherit"
    max_turns: int = 0
    permission_mode: Mode = Mode.DEFAULT
    dont_ask: bool = False
    background: bool = False
    system_prompt: str = ""
    file_path: str = ""
    source: Source = Source.BUILTIN

    def is_fork(self) -> bool:
        return self.name == "__fork__"
