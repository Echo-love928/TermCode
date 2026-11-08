"""读取事件字段并求值 Hook 条件。"""

import json
from typing import Any

from termcode.hook.rule import CombineMode, Condition, Payload


def get_by_path(payload: Payload, path: str) -> str:
    value: Any = payload
    for key in path.split("."):
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def eval_condition(condition: Condition | None, payload: Payload) -> bool:
    if condition is None:
        return True
    matches = (
        atom.matcher.match(get_by_path(payload, atom.field)) for atom in condition.atoms
    )
    return all(matches) if condition.mode is CombineMode.ALL_OF else any(matches)
