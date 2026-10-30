"""加载两层 MCP 配置，只在环境变量与请求头的值中展开变量。"""

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass
class ServerConfig:
    type: Literal["stdio", "http"]
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    servers: dict[str, ServerConfig] = field(default_factory=dict)


def _load_file(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError):
        # YAML 异常包含源文，不能直接打印，以免泄漏凭据。
        print(
            f"[mcp] warn: {path.name} 读取或 YAML 解析失败，跳过该层",
            file=sys.stderr,
        )
        return {}
    if data is None:
        return {}
    if not isinstance(data, dict) or not isinstance(
        servers := data.get("mcp_servers", {}), dict
    ):
        if isinstance(data, dict) and data.get("mcp_servers") is None:
            return {}
        print("[mcp] warn: mcp_servers 必须是映射，跳过该层", file=sys.stderr)
        return {}
    return servers


def _expand_vars(value: str) -> tuple[str, list[str]]:
    undefined = []

    def replace(match: re.Match[str]) -> str:
        name = match[1]
        if name not in os.environ:
            undefined.append(name)
        return os.environ.get(name, "")

    return _VARIABLE.sub(replace, value), undefined


def _validate_server(name: str, raw: Any) -> ServerConfig | None:
    reason = "server 定义必须是映射"
    if isinstance(raw, dict):
        kind = raw.get("type")
        reason = "type 必须为 stdio 或 http"
        if kind in ("stdio", "http"):
            required = "command" if kind == "stdio" else "url"
            value = raw.get(required)
            reason = f"{required} 必须为非空字符串"
            if isinstance(value, str) and value.strip():
                args = raw.get("args", [])
                env = raw.get("env", {})
                headers = raw.get("headers", {})
                if (
                    isinstance(args, list)
                    and all(isinstance(arg, str) for arg in args)
                    and all(
                        isinstance(mapping, dict)
                        and all(
                            isinstance(k, str) and isinstance(v, str)
                            for k, v in mapping.items()
                        )
                        for mapping in (env, headers)
                    )
                ):
                    return ServerConfig(
                        type=kind,
                        command=value if kind == "stdio" else "",
                        args=list(args),
                        env=dict(env),
                        url=value if kind == "http" else "",
                        headers=dict(headers),
                    )
                reason = "args 必须是字符串列表；env / headers 必须是字符串映射"
    print(f"[mcp] warn: skip server {name!r}: {reason}", file=sys.stderr)
    return None


def _apply_expansion(name: str, server: ServerConfig) -> None:
    warned: set[str] = set()
    for mapping in (server.env, server.headers):
        for key, value in mapping.items():
            mapping[key], undefined = _expand_vars(value)
            for variable in undefined:
                if variable not in warned:
                    warned.add(variable)
                    print(
                        f"[mcp] warn: undefined env var ${{{variable}}} "
                        f"referenced by server {name!r}",
                        file=sys.stderr,
                    )


def load_config(root: str) -> Config:
    user = {}
    try:
        user = _load_file(Path.home() / ".termcode/config.yaml")
    except (OSError, RuntimeError):
        print("[mcp] warn: 无法定位用户配置，跳过用户层", file=sys.stderr)
    project = _load_file(Path(root) / ".termcode.yaml")
    config = Config()
    # 完整覆盖后再校验、展开，避免读取已被覆盖的凭据或输出无关告警。
    for name, raw in (user | project).items():
        if not isinstance(name, str) or not name:
            print("[mcp] warn: server 名必须为非空字符串", file=sys.stderr)
            continue
        server = _validate_server(name, raw)
        if server is not None:
            _apply_expansion(name, server)
            config.servers[name] = server
    return config
