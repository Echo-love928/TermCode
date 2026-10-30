"""将本次调用作为精确规则写入本地配置，不自动泛化。"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from termcode.llm import ToolCall
from termcode.permission.rule import Rule, escape_glob, parse_rule
from termcode.permission.sandbox import sandbox_ok
from termcode.permission.settings import extract_target, friendly_name, load_settings
from termcode.permission.types import ApprovalError

if TYPE_CHECKING:
    from termcode.permission.engine import Engine


def rule_for(engine: Engine, call: ToolCall) -> tuple[Rule, str, bool]:
    target, is_file, ok = extract_target(call)
    if not ok or (is_file and not sandbox_ok(engine, target)):
        return Rule(""), "", False
    if is_file:
        target = engine.relative_target(target)
    rule = Rule(friendly_name(call.name), escape_glob(target), True)
    # 无法无损表示的括号宁可报告持久化失败，也不写出不同含义的规则。
    _, valid = parse_rule(str(rule))
    return rule, str(rule), valid


def persist_local_allow(engine: Engine, call: ToolCall) -> None:
    rule, text, ok = rule_for(engine, call)
    if not ok or not engine.local_path or not sandbox_ok(engine, engine.local_path):
        raise ApprovalError("无法生成精确放行规则")
    settings = load_settings(engine.local_path)
    if text not in settings.permissions.allow:
        settings.permissions.allow.append(text)
        path = Path(engine.local_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(asdict(settings), allow_unicode=True), encoding="utf-8"
        )
    if rule not in engine.local.allow:
        engine.local.allow.append(rule)
