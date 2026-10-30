"""配置覆盖、错误降级、变量展开和凭据保护。"""

from pathlib import Path

import pytest
import yaml

from termcode.mcp import load_config


@pytest.fixture
def home(tmp_path, monkeypatch):
    directory = tmp_path / "home"
    directory.mkdir()
    monkeypatch.setattr(Path, "home", lambda: directory)
    return directory


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_layers_missing_and_complete_override(tmp_path, home):
    assert not load_config(str(tmp_path)).servers
    write(
        home / ".termcode/config.yaml",
        {"mcp_servers": {"same": {"type": "stdio", "command": "user", "args": ["x"]}}},
    )
    assert load_config(str(tmp_path)).servers["same"].command == "user"
    write(
        tmp_path / ".termcode.yaml",
        {"mcp_servers": {"same": {"type": "http", "url": "http://localhost/mcp"}}},
    )
    server = load_config(str(tmp_path)).servers["same"]
    assert server.type == "http" and not server.command and not server.args
    (home / ".termcode/config.yaml").unlink()
    assert load_config(str(tmp_path)).servers["same"].type == "http"


@pytest.mark.parametrize(
    "invalid", ["[TEST_SECRET", "[]", "mcp_servers: []", "mcp_servers: false", "\xff"]
)
def test_bad_file_does_not_hide_other_layer(tmp_path, home, invalid, capsys):
    write(
        home / ".termcode/config.yaml",
        {"mcp_servers": {"ok": {"type": "stdio", "command": "x"}}},
    )
    (tmp_path / ".termcode.yaml").write_bytes(invalid.encode("latin1"))
    assert list(load_config(str(tmp_path)).servers) == ["ok"]
    err = capsys.readouterr().err
    assert "warn" in err and "TEST_SECRET" not in err


@pytest.mark.parametrize("data", [None, {}, {"mcp_servers": None}, {"mcp_servers": {}}])
def test_empty_layers(tmp_path, home, data, capsys):
    write(tmp_path / ".termcode.yaml", data)
    assert not load_config(str(tmp_path)).servers
    assert not capsys.readouterr().err


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {},
        {"type": "bad"},
        {"type": []},
        {"type": "stdio"},
        {"type": "http"},
        {"type": "stdio", "command": 12},
        {"type": "stdio", "command": " "},
        {"type": "stdio", "command": "x", "args": "bad"},
        {"type": "stdio", "command": "x", "args": [12]},
        {"type": "stdio", "command": "x", "env": {"K": 12}},
        {"type": "http", "url": "http://localhost", "headers": []},
    ],
)
def test_bad_server_is_skipped(tmp_path, home, raw, capsys):
    write(
        tmp_path / ".termcode.yaml",
        {"mcp_servers": {"bad": raw, "ok": {"type": "stdio", "command": "x"}}},
    )
    assert list(load_config(str(tmp_path)).servers) == ["ok"]
    assert "skip server 'bad'" in capsys.readouterr().err


def test_expand_only_values_once_and_keep_file(tmp_path, home, monkeypatch, capsys):
    monkeypatch.setenv("MCP_DEFINED", "private-value")
    monkeypatch.delenv("MCP_MISSING", raising=False)
    path = tmp_path / ".termcode.yaml"
    write(
        path,
        {
            "mcp_servers": {
                "${MCP_DEFINED}": {
                    "type": "stdio",
                    "command": "${MCP_DEFINED}",
                    "args": ["${MCP_DEFINED}"],
                    "env": {
                        "${MCP_DEFINED}": "${MCP_DEFINED}:${MCP_MISSING}:${MCP_MISSING}"
                    },
                    "headers": {"X": "${MCP_MISSING}"},
                }
            }
        },
    )
    before = path.read_bytes()
    server = load_config(str(tmp_path)).servers["${MCP_DEFINED}"]
    assert server.command == "${MCP_DEFINED}" and server.args == ["${MCP_DEFINED}"]
    assert server.env == {"${MCP_DEFINED}": "private-value::"}
    assert server.headers == {"X": ""}
    err = capsys.readouterr().err
    assert err.count("undefined env var") == 1 and "private-value" not in err
    assert path.read_bytes() == before


def test_home_failure_and_example(tmp_path, home, monkeypatch):
    source = Path(__file__).resolve().parents[1] / "docs/mcp/mcp-servers.example.yaml"
    (tmp_path / ".termcode.yaml").write_bytes(source.read_bytes())
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-value")
    monkeypatch.setenv("EXAMPLE_TOKEN", "fixture-value")

    def no_home():
        raise RuntimeError("not available")

    monkeypatch.setattr(Path, "home", no_home)
    config = load_config(str(tmp_path))
    assert len(config.servers) == 3
    assert (
        config.servers["example-http"].headers["Authorization"]
        == "Bearer fixture-value"
    )
