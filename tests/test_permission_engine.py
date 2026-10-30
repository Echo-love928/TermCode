"""覆盖权限系统的短路、安全默认、规则合并及持久化边界。"""

import itertools
import json
import os
from pathlib import Path

import pytest
import yaml

from termcode.llm import ToolCall
from termcode.permission import Category, Decision, Mode, new_engine, parse_mode
from termcode.permission.blacklist import hits_blacklist
from termcode.permission.engine import mode_fallback
from termcode.permission.rule import Rule, RuleSet, match_pattern, parse_rule
from termcode.permission.settings import (
    SettingsError,
    categorize,
    extract_target,
    load_settings,
    to_rule_set,
)
from termcode.tool import Registry


@pytest.fixture
def engine(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    root = tmp_path / "project"
    root.mkdir()
    return new_engine(str(root))[0]


def call(name, **args):
    return ToolCall("id", name, json.dumps(args))


@pytest.mark.parametrize("mode", list(Mode))
def test_modes_and_complete_matrix(mode):
    assert parse_mode(str(mode).upper()) == (mode, True)
    assert parse_mode("unknown") == (Mode.DEFAULT, False)
    for category in Category:
        expected = (
            Decision.ALLOW
            if (
                category == Category.READ
                or mode == Mode.BYPASS
                or (mode == Mode.ACCEPT_EDITS and category == Category.WRITE)
            )
            else Decision.ASK
        )
        assert mode_fallback(mode, category) == expected


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "rm -fr ~",
        "rm -r -f $HOME",
        'rm -rf "$HOME"',
        "rm --recursive --force /*",
        "sudo rm -rf /; echo done",
        ":(){ :|:& };:",
        "dd if=/dev/zero of=/dev/sda",
        "mkfs.ext4 /dev/sda",
        "echo x > /dev/nvme0n1",
        "chmod -R 777 /",
    ],
)
def test_blacklist_all_modes_and_allow_cannot_override(engine, command):
    engine.local.allow.append(Rule("Bash", "", True))
    for mode in Mode:
        decision, reason = engine.check(mode, call("bash", command=command))
        assert decision == Decision.DENY
        assert "黑名单" in reason


@pytest.mark.parametrize(
    "command", ["rm -rf ./build", "rm -rf /tmp/build", "git status", "ls -la"]
)
def test_blacklist_does_not_overmatch(command):
    assert not hits_blacklist(command)


@pytest.mark.parametrize(
    "name", ["read_file", "write_file", "edit_file", "glob", "grep"]
)
def test_sandbox_all_file_tools_and_relative_rules(engine, name):
    for path in [
        "../outside",
        str(Path(engine.root).parent / "outside"),
        "/etc/passwd",
    ]:
        assert (
            engine.check(Mode.BYPASS, call(name, path=path), True)[0] == Decision.DENY
        )
    assert (
        engine.check(Mode.BYPASS, call(name, path="new/deep/file.txt"), True)[0]
        == Decision.ALLOW
    )
    friendly = {
        "read_file": "Read",
        "write_file": "Write",
        "edit_file": "Edit",
        "glob": "Glob",
        "grep": "Grep",
    }[name]
    engine.user.deny.append(Rule(friendly, "src/**"))
    assert (
        engine.check(
            Mode.BYPASS, call(name, path=str(Path(engine.root) / "src/a/b")), True
        )[0]
        == Decision.DENY
    )
    assert (
        engine.check(Mode.BYPASS, call(name, path="docs/a"), True)[0] == Decision.ALLOW
    )


def test_symlink_existing_and_nonexistent_targets(engine, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("secret")
    link = Path(engine.root) / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"系统不允许创建符号链接：{exc}")
    for target in ["link/secret", "link/new/deep/file", "link/../outside/secret"]:
        assert (
            engine.check(Mode.BYPASS, call("read_file", path=target), True)[0]
            == Decision.DENY
        )
    assert (
        engine.check(Mode.BYPASS, call("write_file", path="nested/new/deep.txt"))[0]
        == Decision.ALLOW
    )


@pytest.mark.parametrize(
    "pattern,target,expected",
    [
        ("git *", "git status", True),
        ("git *", "npm i", False),
        ("git status", "git push", False),
        ("echo **", "echo /a/b c", True),
    ],
)
def test_command_patterns(pattern, target, expected):
    assert match_pattern(pattern, target) == expected


@pytest.mark.parametrize(
    "pattern,target,expected",
    [
        ("src/**", "src/a/b.py", True),
        ("src/*", "src/a/b.py", False),
        ("src/**/*.py", "src/a.py", True),
        ("src/**", "docs/x", False),
        ("", "any/path", True),
    ],
)
def test_path_patterns(pattern, target, expected):
    assert match_pattern(pattern, target, is_file=True) == expected


def test_rule_parsing_and_same_set_priority():
    assert parse_rule("Bash(git *)")[0] == Rule("Bash", "git *")
    assert parse_rule("Read") == (Rule("Read"), True)
    for text in ["", "Bash(", "Bash(git))", "(git)"]:
        assert not parse_rule(text)[1]
    rules = RuleSet(allow=[Rule("Bash")], ask=[Rule("Bash")], deny=[Rule("Bash")])
    assert rules.match("Bash", "git status") == (Decision.DENY, True)
    rules.deny.clear()
    assert rules.match("Bash", "git status") == (Decision.ASK, True)


@pytest.mark.parametrize(
    "order", list(itertools.permutations(("user", "project", "local")))
)
def test_cross_layer_effect_priority(engine, order):
    getattr(engine, order[0]).allow.append(Rule("Bash", "git *", True))
    getattr(engine, order[1]).ask.append(Rule("Bash", "git *"))
    assert (
        engine.check(Mode.BYPASS, call("bash", command="git status"))[0] == Decision.ASK
    )
    getattr(engine, order[2]).deny.append(Rule("Bash", "git push"))
    assert (
        engine.check(Mode.BYPASS, call("bash", command="git push"))[0] == Decision.DENY
    )


def test_short_circuit_and_layer_skipping(engine, monkeypatch):
    def fail(*args):
        pytest.fail("短路后仍访问后续层")

    monkeypatch.setattr("termcode.permission.engine.mode_fallback", fail)
    engine.local.allow.append(Rule("Bash", "git *", True))
    assert (
        engine.check(Mode.DEFAULT, call("bash", command="git status"))[0]
        == Decision.ALLOW
    )
    engine.user.deny.append(Rule("Bash", "git push"))
    assert (
        engine.check(Mode.BYPASS, call("bash", command="git push"))[0] == Decision.DENY
    )
    monkeypatch.setattr(RuleSet, "matching_rule", fail)
    assert (
        engine.check(Mode.BYPASS, call("bash", command="rm -rf /"))[0] == Decision.DENY
    )
    assert (
        engine.check(Mode.BYPASS, call("read_file", path="../out"), True)[0]
        == Decision.DENY
    )


@pytest.mark.parametrize("mode", list(Mode))
def test_safe_defaults_cannot_be_overridden(engine, mode):
    engine.local.allow.extend([Rule("Bash", "", True), Rule("missing", "", True)])
    for tool in [
        ToolCall("id", "bash", "invalid"),
        call("bash"),
        call("bash", command=123),
        call("missing"),
    ]:
        assert engine.check(mode, tool)[0] == Decision.DENY
    for raw in ["{", "[]", "null", "{}", '{"path": null}']:
        assert (
            engine.check(mode, ToolCall("id", "read_file", raw), True)[0]
            == Decision.DENY
        )
    assert categorize("read_file", None) == Category.EXEC
    assert categorize("bash", True) == Category.READ
    engine.project.ask.append(Rule("Read"))
    assert engine.check(mode, call("read_file", path="a"), True)[0] == Decision.DENY


def test_config_degradation_default_priority_and_ask(engine, monkeypatch):
    root = Path(engine.root)
    paths = [
        Path.home() / ".termcode/settings.yaml",
        root / ".termcode/settings.yaml",
        Path(engine.local_path),
    ]
    assert new_engine(str(root))[0].start_mode() == Mode.DEFAULT
    for path, mode in zip(
        paths, ["plan", "acceptEdits", "bypassPermissions"], strict=True
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(
                {"default_mode": mode, "permissions": {"ask": ["Bash(git *)"]}}
            )
        )
        loaded, err = new_engine(str(root))
        assert err is None and str(loaded.start_mode()) == mode
        assert (
            loaded.check(Mode.BYPASS, call("bash", command="git status"))[0]
            == Decision.ASK
        )
    paths[-1].write_text("permissions: [broken")
    assert new_engine(str(root))[0].start_mode() == Mode.ACCEPT_EDITS
    for bad in [
        "[]",
        "default_mode: []",
        "permissions: []",
        "permissions: {allow: all}",
    ]:
        paths[-1].write_text(bad)
        with pytest.raises(SettingsError):
            load_settings(str(paths[-1]))
        assert new_engine(str(root))[1] is None
    paths[-1].write_text('permissions: {allow: ["Bash(", "Bash(git *)"]}')
    assert len(to_rule_set(load_settings(str(paths[-1]))).allow) == 1
    broken, err = new_engine(str(root / "nonexistent"))
    assert err is not None and broken is not None
    assert (
        broken.check(Mode.BYPASS, call("read_file", path="file"), True)[0]
        == Decision.DENY
    )


def test_search_root_defaults_and_bash_without_sandbox(engine):
    for name in ["glob", "grep"]:
        assert extract_target(call(name, pattern="../*")) == (".", True, True)
    assert (
        engine.check(Mode.BYPASS, call("bash", command="cat /etc/passwd"))[0]
        == Decision.ALLOW
    )


def test_registry_missing_readonly_flag_is_not_trusted():
    class MissingFlag:
        def name(self):
            return "read_file"

    registry = Registry()
    registry.register(MissingFlag())
    assert registry.is_read_only("read_file") is False
    assert registry.is_read_only("unregistered") is False


def test_permanent_allow_preserves_stricter_rules_and_mode(engine):
    path = Path(engine.local_path)
    path.parent.mkdir()
    path.write_text(
        "default_mode: plan\npermissions:\n"
        "  ask: ['Bash(git *)']\n  deny: ['Bash(git push)']\n"
    )
    tool = call("bash", command="git status")
    engine.persist_local_allow(tool)
    reloaded = new_engine(engine.root)[0]
    assert reloaded.start_mode() == Mode.PLAN
    assert reloaded.check(Mode.BYPASS, tool)[0] == Decision.ASK
    assert (
        reloaded.check(Mode.BYPASS, call("bash", command="git push"))[0]
        == Decision.DENY
    )


@pytest.mark.parametrize(
    "name,target,other",
    [
        ("bash", "echo *", "echo everything"),
        ("bash", r"echo C:\temp\*", r"echo C:\temp\other"),
        ("bash", "echo (hello)", "echo hello"),
        ("write_file", "literal*.txt", "literalOTHER.txt"),
    ],
)
def test_persist_exact_glob_escaping_reload_and_idempotence(
    engine, name, target, other
):
    if name != "bash" and os.name == "nt":
        pytest.skip("Windows 文件名不支持星号，Linux 验证该分支")
    key = "command" if name == "bash" else "path"
    tool = call(name, **{key: target})
    engine.persist_local_allow(tool)
    before = Path(engine.local_path).read_text()
    engine.persist_local_allow(tool)
    assert Path(engine.local_path).read_text() == before
    reloaded = new_engine(engine.root)[0]
    assert reloaded.check(Mode.DEFAULT, tool)[0] == Decision.ALLOW
    assert reloaded.check(Mode.DEFAULT, call(name, **{key: other}))[0] == Decision.ASK
