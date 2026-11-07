"""解析带 YAML frontmatter 的 Skill Markdown。"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import yaml

SKILL_NAME = re.compile(r"[a-z][a-z0-9-]*")


class SkillParseError(ValueError):
    """Skill 文件无法读取或格式无效。"""


@dataclass(frozen=True)
class SkillDef:
    name: str
    description: str
    prompt_body: str
    source_path: Path
    is_directory: bool = False
    mode: str = "inline"
    model: str | None = None
    context: str = "full"
    frontmatter: dict[str, object] = field(
        default_factory=dict, compare=False, repr=False
    )


def parse_frontmatter(raw: str) -> tuple[dict[str, object], str]:
    lines = raw.splitlines(keepends=True)
    if not lines or lines[0].removeprefix("\ufeff").rstrip() != "---":
        raise SkillParseError("缺少起始 frontmatter 分隔符")
    index = next(
        (i for i, line in enumerate(lines[1:], 1) if line.rstrip() == "---"), None
    )
    if index is None:
        raise SkillParseError("frontmatter 未闭合")
    try:
        meta = yaml.safe_load("".join(lines[1:index]))
    except yaml.YAMLError:
        raise SkillParseError("frontmatter YAML 格式错误") from None
    if not isinstance(meta, dict) or not all(isinstance(key, str) for key in meta):
        raise SkillParseError("frontmatter 必须是字符串键的映射")
    return meta, "".join(lines[index + 1 :]).strip("\r\n")


def _validate_meta(meta: dict[str, object]) -> tuple[str, str, str, str | None, str]:
    name, description = meta.get("name"), meta.get("description")
    mode, model, context = (
        meta.get("mode", "inline"),
        meta.get("model"),
        meta.get("context", "full"),
    )
    if not isinstance(name, str) or not SKILL_NAME.fullmatch(name):
        raise SkillParseError("name 必须以小写字母开头，仅可含小写字母、数字和连字符")
    if not isinstance(description, str) or not description.strip():
        raise SkillParseError("description 必须是非空字符串")
    if mode not in ("inline", "fork"):
        raise SkillParseError("mode 必须为 inline 或 fork")
    if context not in ("full", "recent", "none"):
        raise SkillParseError("context 必须为 full、recent 或 none")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise SkillParseError("model 必须是非空字符串")
    return name, description.strip(), cast(str, mode), model, cast(str, context)


def parse_skill_file(path: Path, *, is_directory: bool | None = None) -> SkillDef:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SkillParseError(f"无法读取 Skill 文件 ({type(exc).__name__})") from None
    meta, body = parse_frontmatter(raw)
    name, description, mode, model, context = _validate_meta(meta)
    return SkillDef(
        name=name,
        description=description,
        prompt_body=body,
        source_path=path.absolute(),
        is_directory=path.name == "SKILL.md" if is_directory is None else is_directory,
        mode=mode,
        model=model,
        context=context,
        frontmatter=meta,
    )


def substitute_arguments(prompt_body: str, args: str) -> str:
    return prompt_body.replace("$ARGUMENTS", args)
