"""友好工具规则、命令 glob 与文件路径 glob。"""

import re
from dataclasses import dataclass, field

from termcode.permission.types import Decision


@dataclass(frozen=True)
class Rule:
    tool: str
    pattern: str = ""
    allow: bool = False

    def __str__(self) -> str:
        return f"{self.tool}({self.pattern})" if self.pattern else self.tool


def parse_rule(value: str) -> tuple[Rule, bool]:
    if not isinstance(value, str):
        return Rule(""), False
    match = re.fullmatch(
        r"(mcp__[A-Za-z0-9_*\-]+|[A-Za-z_][\w]*)(?:\((.*)\))?",
        value.strip(),
        re.DOTALL,
    )
    if match is None:
        return Rule(""), False
    pattern = match[2] or ""
    # 命令中的成对括号是字面内容，不能只按第一个右括号截断。
    unescaped = re.sub(r"\\.", "", pattern)
    if unescaped.count("(") != unescaped.count(")"):
        return Rule(""), False
    return Rule(match[1], pattern), True


def escape_glob(value: str) -> str:
    return re.sub(r"([\\*()])", r"\\\1", value)


def match_pattern(pattern: str, target: str, *, is_file: bool = False) -> bool:
    if not pattern:
        return True
    regex = ""
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\" and index + 1 < len(pattern):
            index += 1
            regex += re.escape(pattern[index])
        elif char == "*":
            double = index + 1 < len(pattern) and pattern[index + 1] == "*"
            if double:
                index += 1
            if is_file and double and pattern[index + 1 : index + 2] == "/":
                regex += "(?:.*/)?"
                index += 1
            else:
                regex += ".*" if double or not is_file else "[^/]*"
        else:
            regex += re.escape(char)
        index += 1
    return re.fullmatch(regex, target, re.DOTALL) is not None


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
                if tool_matches and match_pattern(
                    rule.pattern, target, is_file=friendly != "Bash"
                ):
                    return decision, rule
        return Decision.ALLOW, None

    def match(self, friendly: str, target: str) -> tuple[Decision, bool]:
        decision, rule = self.matching_rule(friendly, target)
        return decision, rule is not None
