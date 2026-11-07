"""启动时校验名字和别名，统一提供帮助和补全数据。"""

from .command import Command


class Registry:
    def __init__(self) -> None:
        self._by_name: dict[str, Command] = {}
        self._visible: list[Command] = []

    def register(self, cmd: Command) -> None:
        keys = (cmd.name, *cmd.aliases)
        seen: set[str] = set()
        for key in keys:
            if (
                not key
                or key != key.lower()
                or "/" in key
                or any(c.isspace() for c in key)
            ):
                raise ValueError(f"无效命令名或别名: {key!r}")
            if key in self._by_name or key in seen:
                raise RuntimeError(f"command conflict: {key}")
            seen.add(key)
        # 整条命令校验通过后再提交，冲突不会留下半条注册记录。
        self._by_name.update(dict.fromkeys(keys, cmd))
        if not cmd.hidden:
            self._visible.append(cmd)
            self._visible.sort(key=lambda command: command.name)

    def lookup(self, name: str) -> Command | None:
        return self._by_name.get(name.lower())

    def visible(self) -> list[Command]:
        return list(self._visible)

    def prefix_match(self, prefix: str) -> list[Command]:
        value = prefix.strip().removeprefix("/").lower()
        return [cmd for cmd in self._visible if cmd.name.startswith(value)]
