"""黑名单、沙箱、合并规则与模式兜底的前四层流水线。"""

from dataclasses import dataclass, field
from pathlib import Path

from termcode.llm import ToolCall
from termcode.permission.blacklist import hits_blacklist
from termcode.permission.rule import RuleSet
from termcode.permission.sandbox import (
    eval_symlinks_or_ancestor,
    resolve_root,
    sandbox_ok,
)
from termcode.permission.settings import (
    FRIENDLY_NAMES,
    Settings,
    SettingsError,
    categorize,
    extract_target,
    friendly_name,
    load_settings,
    to_rule_set,
)
from termcode.permission.types import Category, Decision, Mode, parse_mode


def mode_fallback(mode: Mode, category: Category) -> Decision:
    if category == Category.READ or mode == Mode.BYPASS:
        return Decision.ALLOW
    if mode == Mode.ACCEPT_EDITS and category == Category.WRITE:
        return Decision.ALLOW
    return Decision.ASK


@dataclass
class Engine:
    root: str
    user: RuleSet = field(default_factory=RuleSet)
    project: RuleSet = field(default_factory=RuleSet)
    local: RuleSet = field(default_factory=RuleSet)
    local_path: str = ""
    _start_mode: Mode = Mode.DEFAULT
    root_valid: bool = True

    def start_mode(self) -> Mode:
        return self._start_mode

    def relative_target(self, target: str) -> str:
        path = Path(target).expanduser()
        if not path.is_absolute():
            path = Path(self.root) / path
        return (
            Path(eval_symlinks_or_ancestor(str(path))).relative_to(self.root).as_posix()
        )

    def check(
        self, mode: Mode, call: ToolCall, read_only: bool | None = False
    ) -> tuple[Decision, str]:
        category = categorize(call.name, read_only)
        friendly = friendly_name(call.name)
        target, is_file, ok = extract_target(call)
        # 真正的命令始终先检查黑名单，不依赖外部误标的只读属性。
        if call.name == "bash" and target and hits_blacklist(target):
            return Decision.DENY, "命中危险命令黑名单：" + target
        if is_file:
            if not ok:
                return Decision.DENY, "无法解析文件路径参数，安全拒绝"
            if not sandbox_ok(self, target):
                return Decision.DENY, "路径在项目目录之外：" + target
            try:
                target = self.relative_target(target)
            except (OSError, RuntimeError, ValueError):
                return Decision.DENY, "无法解析文件路径参数，安全拒绝"
        elif call.name not in FRIENDLY_NAMES:
            return Decision.DENY, "未知工具，安全拒绝：" + call.name
        elif not ok:
            return Decision.DENY, "无法解析命令参数，安全拒绝"
        # 三层合并后先比较裁决效果，层级不覆盖更严格的规则。
        matches = [
            rules.matching_rule(friendly, target)
            for rules in (self.user, self.project, self.local)
        ]
        for decision in (Decision.DENY, Decision.ASK, Decision.ALLOW):
            for result, rule in matches:
                if rule is not None and result == decision:
                    if decision == Decision.ASK and category == Category.READ:
                        return Decision.DENY, f"只读工具匹配 ask 规则，安全拒绝：{rule}"
                    kind = "deny" if decision == Decision.DENY else "ask"
                    return decision, (
                        ""
                        if decision == Decision.ALLOW
                        else f"匹配 {kind} 规则：{rule}"
                    )
        decision = mode_fallback(mode, category)
        return decision, (
            ""
            if decision == Decision.ALLOW
            else f"{mode} 模式下 {category.name} 类操作需确认"
        )

    def persist_local_allow(self, call: ToolCall) -> None:
        from termcode.permission.persist import persist_local_allow

        persist_local_allow(self, call)


def new_engine(root: str) -> tuple[Engine, Exception | None]:
    try:
        resolved = resolve_root(root)
    except (OSError, RuntimeError, ValueError) as exc:
        return Engine(root, root_valid=False), exc
    paths = [
        Path.home() / ".termcode/settings.yaml",
        Path(resolved) / ".termcode/settings.yaml",
        Path(resolved) / ".termcode/settings.local.yaml",
    ]
    settings = []
    for path in paths:
        try:
            settings.append(load_settings(str(path)))
        except (SettingsError, UnicodeError):
            settings.append(Settings())
    mode = Mode.DEFAULT
    for entry in reversed(settings):
        parsed, ok = parse_mode(entry.default_mode)
        if ok:
            mode = parsed
            break
    return Engine(
        root=resolved,
        user=to_rule_set(settings[0]),
        project=to_rule_set(settings[1]),
        local=to_rule_set(settings[2]),
        local_path=str(paths[2]),
        _start_mode=mode,
    ), None
