"""与渲染框架无关的斜杠命令系统。"""

from .builtins import register_builtins
from .command import Command, Handler, Kind
from .dispatch import parse
from .registry import Registry
from .ui import UI, NopUI

__all__ = [
    "Command",
    "Handler",
    "Kind",
    "Registry",
    "UI",
    "NopUI",
    "parse",
    "register_builtins",
]
