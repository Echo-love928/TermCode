import re
import sys
from pathlib import Path

from termcode.permission.types import parse_mode
from termcode.skills.parser import parse_frontmatter

from .definition import MODELS, Definition, Source

AGENT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}")


def _tool_names(meta: dict[str, object], key: str) -> list[str]:
    values = meta.get(key, [])
    if not isinstance(values, list) or not all(
        isinstance(value, str) and value.strip() for value in values
    ):
        raise ValueError(f"{key} 必须是工具名列表")
    return [value.strip() for value in values]


def parse_definition(data: bytes, file_path: str, source: Source) -> Definition:
    meta, body = parse_frontmatter(data.decode("utf-8-sig"))
    name, description = meta.get("name"), meta.get("description")
    if not isinstance(name, str) or not AGENT_NAME.fullmatch(name.strip()):
        raise ValueError("name 必须以字母开头，最多 32 字符，仅允许字母、数字、- 和 _")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description 必须是非空字符串")
    model = meta.get("model", "inherit")
    if not isinstance(model, str) or model not in MODELS:
        print(
            f'unknown model "{model}" in {file_path}, defaulting to inherit',
            file=sys.stderr,
        )
        model = "inherit"
    max_turns = meta.get("maxTurns", 0)
    if type(max_turns) is not int or max_turns < 0:
        raise ValueError("maxTurns 必须是非负整数")
    raw_mode = meta.get("permissionMode", "default")
    dont_ask = isinstance(raw_mode, str) and raw_mode.casefold() == "dontask"
    mode, valid = parse_mode(raw_mode if isinstance(raw_mode, str) else "")
    if not valid and not dont_ask:
        print(
            f'unknown permissionMode "{raw_mode}" in {file_path}, '
            "defaulting to default",
            file=sys.stderr,
        )
    background = meta.get("background", False)
    if not isinstance(background, bool):
        raise ValueError("background 必须是布尔值")
    return Definition(
        name=name.strip(),
        description=description.strip(),
        tools=_tool_names(meta, "tools"),
        disallowed_tools=_tool_names(meta, "disallowedTools"),
        model=model,
        max_turns=max_turns,
        permission_mode=mode,
        dont_ask=dont_ask,
        background=background,
        system_prompt=body,
        file_path=file_path,
        source=source,
    )


def parse_file(path: str | Path, source: Source) -> Definition:
    path = Path(path).absolute()
    return parse_definition(path.read_bytes(), str(path), source)
