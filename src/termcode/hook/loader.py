"""启动时从项目与用户目录加载 Hook，逐条隔离无效配置。"""

import math
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from termcode.hook.engine import Engine
from termcode.hook.event import Event, is_blocking
from termcode.hook.rule import (
    Action,
    ActionType,
    AtomCondition,
    CombineMode,
    Condition,
    HttpAction,
    PromptAction,
    Rule,
    ShellAction,
    SubagentAction,
)
from termcode.permission.matcher import (
    ExactMatcher,
    GlobMatcher,
    Matcher,
    NotMatcher,
    RegexMatcher,
)


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a mapping")
    return value


def _string(value: object, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(
            f"{field} must be {'a' if allow_empty else 'a non-empty'} string"
        )
    return value


def _flag(raw: dict[str, Any], field: str) -> bool:
    value = raw.get(field, False)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _parse_duration(value: object) -> float:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([smh]?)", str(value))
    if match:
        seconds = float(match[1]) * {"": 1, "s": 1, "m": 60, "h": 3600}[match[2]]
        if math.isfinite(seconds) and seconds > 0:
            return seconds
    raise ValueError("timeout must be a positive duration, e.g. 30s")


def _compile_matcher(value: object) -> Matcher:
    raw = _mapping(value, "match")
    kind = raw.get("type")
    if kind == "not":
        return NotMatcher(_compile_matcher(raw.get("inner")))
    if kind not in ("exact", "glob", "regex"):
        raise ValueError(f"unknown match type {kind!r}")
    pattern = _string(raw.get("value"), "match.value", allow_empty=True)
    if kind == "exact":
        return ExactMatcher(pattern)
    if kind == "regex":
        return RegexMatcher(pattern)
    return GlobMatcher(pattern, is_command=False)


def _compile_condition(value: object) -> Condition:
    raw = _mapping(value, "if")
    if len(raw) != 1 or next(iter(raw)) not in ("all_of", "any_of"):
        raise ValueError("if must contain exactly one of all_of or any_of")
    mode = CombineMode(next(iter(raw)))
    items = raw[mode.value]
    if not isinstance(items, list):
        raise ValueError(f"if.{mode} must be a list")
    atoms = []
    for item in items:
        atom = _mapping(item, "condition")
        field = _string(atom.get("field"), "condition.field")
        if set(atom) != {"field", "match"} or not all(field.split(".")):
            raise ValueError("condition must contain a field path and match")
        atoms.append(AtomCondition(field, _compile_matcher(atom["match"])))
    return Condition(mode, tuple(atoms))


def _compile_action(value: object) -> Action:
    raw = _mapping(value, "action")
    match raw.get("type"):
        case ActionType.SHELL:
            return ShellAction(_string(raw.get("command"), "action.command"))
        case ActionType.PROMPT:
            return PromptAction(
                _string(raw.get("text"), "action.text", allow_empty=True)
            )
        case ActionType.HTTP:
            headers = _mapping(raw.get("headers", {}), "action.headers")
            if any(
                not isinstance(k, str) or not isinstance(v, str)
                for k, v in headers.items()
            ):
                raise ValueError("action.headers must contain string keys and values")
            return HttpAction(
                _string(raw.get("url"), "action.url"),
                _string(raw.get("method", "POST"), "action.method"),
                headers,
                _string(raw["body"], "action.body", allow_empty=True)
                if "body" in raw
                else None,
            )
        case ActionType.SUBAGENT:
            return SubagentAction(
                _string(raw.get("agent_name"), "action.agent_name"),
                _string(raw.get("prompt"), "action.prompt", allow_empty=True),
            )
        case other:
            raise ValueError(f"unknown action type {other!r}")


def _compile_rule(raw: dict[str, Any], source: str) -> Rule:
    name = _string(raw.get("name"), "name")
    event_name = _string(raw.get("event"), "event")
    try:
        event = Event(event_name)
    except ValueError:
        raise ValueError(f'unknown event "{event_name}"') from None
    background = _flag(raw, "async")
    if background and is_blocking(event):
        raise ValueError("async not allowed for blocking events")
    return Rule(
        name,
        event,
        _compile_action(raw.get("action")),
        _compile_condition(raw["if"]) if "if" in raw else None,
        _flag(raw, "only_once"),
        background,
        _parse_duration(raw.get("timeout", "30s")),
        source,
    )


def load(project_root: str | Path) -> Engine:
    paths = (
        Path(project_root).absolute() / ".termcode/hooks.yaml",
        Path.home() / ".termcode/hooks.yaml",
    )
    rules: list[Rule] = []
    sources: list[str] = []
    names: set[str] = set()
    for path in dict.fromkeys(paths):
        source = str(path)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            entries = _mapping(data, "hooks file").get("hooks")
            if not isinstance(entries, list):
                raise ValueError("hooks must be a list")
        except FileNotFoundError:
            continue
        except (OSError, ValueError, yaml.YAMLError, RecursionError) as exc:
            print(f"hooks {source}: {exc}", file=sys.stderr)
            continue
        sources.append(source)
        for index, entry in enumerate(entries, start=1):
            name = entry.get("name") if isinstance(entry, dict) else None
            try:
                rule = _compile_rule(_mapping(entry, "hook"), source)
                if rule.name in names:
                    raise ValueError("duplicate name")
            except (ValueError, RecursionError) as exc:
                label = f'"{name}"' if isinstance(name, str) else f"#{index}"
                print(f"hook {label}: {exc}, skipped ({source})", file=sys.stderr)
                continue
            rules.append(rule)
            names.add(rule.name)
    return Engine(rules, sources)
