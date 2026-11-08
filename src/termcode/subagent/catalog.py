from __future__ import annotations

import builtins
import sys
from importlib.resources import files
from pathlib import Path

from .definition import Definition, Source
from .parser import parse_definition, parse_file

BUILTINS = ("general-purpose", "explore", "plan")


class Catalog:
    """启动期加载的角色目录，角色名忽略大小写。"""

    def __init__(self) -> None:
        self._defs: dict[str, Definition] = {}
        self._by_source: dict[Source, list[Definition]] = {}

    def _add(self, definition: Definition) -> None:
        self._by_source.setdefault(definition.source, []).append(definition)
        self._defs[definition.name.casefold()] = definition

    def resolve(self, name: str) -> Definition | None:
        return self._defs.get(name.casefold())

    def list(self) -> list[Definition]:
        return [self._defs[name] for name in sorted(self._defs)]

    def list_by_source(self, source: Source) -> builtins.list[Definition]:
        return self._by_source.get(source, [])[:]

    def fork_definition(self) -> Definition:
        return Definition("__fork__", "继承父对话的后台任务", background=True)


def load_catalog(root: str | Path) -> Catalog:
    catalog = Catalog()
    builtin = files("termcode.subagent").joinpath("builtin")
    for name in BUILTINS:
        path = builtin.joinpath(f"{name}.md")
        catalog._add(parse_definition(path.read_bytes(), str(path), Source.BUILTIN))
    for source, folder in ((Source.USER, Path.home()), (Source.PROJECT, Path(root))):
        for path in sorted((folder / ".termcode/agents").glob("*.md")):
            try:
                catalog._add(parse_file(path, source))
            except (OSError, ValueError) as exc:
                print(f"无法加载子 Agent {path}：{exc}", file=sys.stderr)
    return catalog
