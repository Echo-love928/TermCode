"""三层 YAML 配置加载、工具分类和目标提取。"""

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from termcode.llm import ToolCall
from termcode.permission.rule import RuleSet, parse_rule
from termcode.permission.types import Category

FRIENDLY_NAMES = {
    "bash": "Bash",
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Edit",
    "glob": "Glob",
    "grep": "Grep",
}
FILE_TOOLS = frozenset(FRIENDLY_NAMES) - {"bash"}


class SettingsError(Exception):
    """配置读取或结构解析失败，不携带配置原文。"""


@dataclass
class PermissionsBlock:
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    ask: list[str] = field(default_factory=list)


@dataclass
class Settings:
    default_mode: str = ""
    permissions: PermissionsBlock = field(default_factory=PermissionsBlock)


def load_settings(path: str) -> Settings:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return Settings()
    except OSError:
        raise SettingsError("无法读取权限配置") from None
    try:
        data = yaml.safe_load(raw)
        if data is None:
            return Settings()
        if not isinstance(data, dict):
            raise ValueError
        mode = data.get("default_mode", "")
        block = data.get("permissions", {})
        if not isinstance(mode, str) or not isinstance(block, dict):
            raise ValueError
        entries = {}
        for key in ("allow", "deny", "ask"):
            values = block.get(key, [])
            if not isinstance(values, list) or any(
                not isinstance(v, str) for v in values
            ):
                raise ValueError
            entries[key] = values
        return Settings(mode, PermissionsBlock(**entries))
    except (yaml.YAMLError, ValueError, TypeError):
        raise SettingsError("权限配置格式错误") from None


def to_rule_set(settings: Settings) -> RuleSet:
    rules = RuleSet()
    for key in ("allow", "deny", "ask"):
        for value in getattr(settings.permissions, key):
            rule, ok = parse_rule(value)
            if ok:
                getattr(rules, key).append(replace(rule, allow=key == "allow"))
    return rules


def friendly_name(internal: str) -> str:
    return FRIENDLY_NAMES.get(internal, internal)


def categorize(internal: str, read_only: bool | None = False) -> Category:
    if read_only is True:
        return Category.READ
    if internal in {"write_file", "edit_file"}:
        return Category.WRITE
    return Category.EXEC


def extract_target(call: ToolCall) -> tuple[str, bool, bool]:
    is_file = call.name in FILE_TOOLS
    try:
        data = json.loads(call.input) if isinstance(call.input, str) else call.input
        if not isinstance(data, dict):
            return "", is_file, False
        if is_file:
            target = data.get("path", "." if call.name in {"glob", "grep"} else "")
            if call.name in {"glob", "grep"} and target == "":
                target = "."
        elif call.name == "bash":
            target = data.get("command", "")
        else:
            return "", False, False
        ok = isinstance(target, str) and bool(target.strip()) and "\x00" not in target
        return target if ok else "", is_file, ok
    except (ValueError, TypeError):
        return "", is_file, False
