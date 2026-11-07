"""启动时校验名字和别名，统一提供帮助和补全数据。"""

from .command import Command


class Registry:
    def __init__(self) -> None:
        self._by_name: dict[str, Command] = {}
        self._visible: list[Command] = []
        self._skill_commands: dict[str, Command] = {}

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

    def set_skill_commands(self, commands: list[Command]) -> None:
        # Skill 覆盖只作用于当前注册表，重载移除后原命令自动恢复。
        self._skill_commands = {cmd.name: cmd for cmd in commands}

    def lookup(self, name: str, *, base_only: bool = False) -> Command | None:
        key = name.lower()
        if not base_only and key in self._skill_commands:
            return self._skill_commands[key]
        return self._by_name.get(key)

    def visible(self) -> list[Command]:
        if not self._skill_commands:
            return list(self._visible)
        visible = {cmd.name: cmd for cmd in self._visible}
        visible.update(self._skill_commands)
        return sorted(visible.values(), key=lambda cmd: cmd.name)

    def prefix_match(self, prefix: str) -> list[Command]:
        value = prefix.strip().removeprefix("/").lower()
        return [cmd for cmd in self.visible() if cmd.name.startswith(value)]
