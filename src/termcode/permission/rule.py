"""友好工具规则、命令 glob 与文件路径 glob。"""

import re
from dataclasses import dataclass, field

from termcode.permission.matcher import (
    Matcher,
    compile_matcher,
)
from termcode.permission.matcher import (
    match_pattern as match_pattern,
)
from termcode.permission.types import Decision


@dataclass(frozen=True)
class Rule:
    tool: str
    matcher: Matcher | None = None
    allow: bool = False
    raw: str = field(default="", compare=False)

    def __str__(self) -> str:
        if self.raw:
            return self.raw
        return f"{self.tool}({self.matcher})" if self.matcher is not None else self.tool


def parse_rule(value: object) -> tuple[Rule | None, str | None]:
    if not isinstance(value, str):
        return None, "expected a string"
    match = re.fullmatch(
        r"(mcp__[A-Za-z0-9_*\-]+|[A-Za-z_][\w]*)(?:\((.*)\))?",
        value.strip(),
        re.DOTALL,
    )
    if match is None:
        return None, "expected Tool or Tool(pattern)"
    pattern = match[2] or ""
    # 旧 glob 保留括号校验；正则交由编译器校验，精确值按字面处理。
    glob = pattern.lstrip("!")
    if glob and glob[0] not in "=~":
        unescaped = re.sub(r"\\.", "", glob)
        if unescaped.count("(") != unescaped.count(")"):
            return None, "unbalanced parentheses"
    try:
        matcher = (
            compile_matcher(pattern, is_command=match[1] == "Bash") if pattern else None
        )
    except (ValueError, RecursionError) as exc:
        return None, str(exc)
    return Rule(match[1], matcher, raw=value.strip()), None


def escape_glob(value: str) -> str:
    return re.sub(r"([\\*()]|^[=!~])", r"\\\1", value)


@dataclass
class RuleSet:
    allow: list[Rule] = field(default_factory=list)
    deny: list[Rule] = field(default_factory=list)
    ask: list[Rule] = field(default_factory=list)

    def matching_rule(self, friendly: str, target: str) -> tuple[Decision, Rule | None]:
        for decision, rules in (
            (Decision.DENY, self.deny),
            (Decision.ASK, self.ask),
            (Decision.ALLOW, self.allow),
        ):
            for rule in rules:
                tool_matches = rule.tool == friendly or (
                    friendly.startswith("mcp__")
                    and rule.tool.startswith("mcp__")
                    and match_pattern(rule.tool, friendly)
                )
                if tool_matches and (
                    rule.matcher is None or rule.matcher.match(target)
                ):
                    return decision, rule
        return Decision.ALLOW, None

    def match(self, friendly: str, target: str) -> tuple[Decision, bool]:
        decision, rule = self.matching_rule(friendly, target)
        return decision, rule is not None
