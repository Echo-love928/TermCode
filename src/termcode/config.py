"""读取并校验项目内的供应商配置。"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit

import yaml


class ConfigError(Exception):
    """可直接向用户展示的配置错误，不包含配置原文。"""


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    protocol: Literal["anthropic", "openai"]
    api_key: str = field(repr=False)
    model: str
    base_url: str | None = None
    thinking: bool = False


@dataclass
class Config:
    providers: list[ProviderConfig] = field(default_factory=list)


def load(path: str | Path) -> Config:
    """加载 YAML；格式错误只报告位置，避免泄露含密钥的源代码行。"""
    try:
        content = Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise ConfigError(f"配置文件不存在: {path}") from None
    except (OSError, UnicodeError):
        raise ConfigError(f"无法读取 UTF-8 配置文件: {path}") from None
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f"（第 {mark.line + 1} 行，第 {mark.column + 1} 列）" if mark else ""
        raise ConfigError(f"YAML 格式错误{location}") from None
    return _from_dict(data)


def _from_dict(data: object) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("配置根节点必须是映射，且包含 providers 列表")
    entries = data.get("providers")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("providers 必须是非空列表")
    providers: list[ProviderConfig] = []
    names: set[str] = set()
    allowed = {"name", "protocol", "model", "api_key", "base_url", "thinking"}
    for index, entry in enumerate(entries):
        prefix = f"providers[{index}]"
        if not isinstance(entry, dict):
            raise ConfigError(f"{prefix} 必须是映射")
        if set(entry) - allowed:
            raise ConfigError(f"{prefix} 包含不支持的字段，请检查字段拼写")
        values: dict[str, str] = {}
        for key in ("name", "protocol", "api_key", "model"):
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ConfigError(f"{prefix}.{key} 必须是非空字符串")
            values[key] = value.strip()
        if values["protocol"] not in {"anthropic", "openai"}:
            raise ConfigError(f"{prefix}.protocol 必须是 anthropic 或 openai")
        if values["name"] in names:
            raise ConfigError(f"{prefix}.name 与已有配置重名")
        names.add(values["name"])
        thinking = entry.get("thinking", False)
        if not isinstance(thinking, bool):
            raise ConfigError(f"{prefix}.thinking 必须是布尔值 true 或 false")
        base_url = entry.get("base_url")
        if base_url is not None:
            if not isinstance(base_url, str) or not base_url.strip():
                raise ConfigError(f"{prefix}.base_url 必须是非空 HTTP(S) 地址或 null")
            base_url = base_url.strip()
            try:
                parts = urlsplit(base_url)
                valid = parts.scheme in {"http", "https"} and bool(parts.hostname)
                valid = valid and not (
                    parts.username or parts.password or parts.fragment
                )
                _ = parts.port
            except ValueError:
                valid = False
            if not valid:
                raise ConfigError(f"{prefix}.base_url 必须是合法的 HTTP(S) 地址")
        providers.append(
            ProviderConfig(
                name=values["name"],
                protocol=cast(Literal["anthropic", "openai"], values["protocol"]),
                api_key=values["api_key"],
                model=values["model"],
                base_url=base_url,
                thinking=thinking,
            )
        )
    return Config(providers)
