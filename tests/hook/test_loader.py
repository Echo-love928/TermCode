"""验证两层 Hook 配置的编译和逐条降级。"""

from pathlib import Path

import pytest
import yaml

from termcode.hook import Event, load
from termcode.hook.matcher import get_by_path


def save(root, rules):
    path = root / ".termcode/hooks.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"hooks": rules}), encoding="utf-8")
    return str(path)


@pytest.fixture
def roots(tmp_path, monkeypatch):
    project, home = tmp_path / "project", tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    return project, home


def hook(name, **overrides):
    return {
        "name": name,
        "event": "PreToolUse",
        "action": {"type": "prompt", "text": name},
        **overrides,
    }


async def test_project_then_user_and_nested_conditions(roots, capsys):
    project, home = roots
    condition = {
        "all_of": [
            {"field": "tool_input.path", "match": {"type": "glob", "value": "**/*.py"}},
            {
                "field": "missing.key",
                "match": {
                    "type": "not",
                    "inner": {
                        "type": "not",
                        "inner": {"type": "exact", "value": ""},
                    },
                },
            },
        ]
    }
    project_source = save(project, [hook("first", **{"if": condition})])
    user_source = save(home, [hook("first"), hook("second", timeout="0.5m")])
    engine = load(project)
    assert engine.sources == [project_source, user_source]
    assert [(rule.name, rule.timeout_s) for rule in engine.rules] == [
        ("first", 30.0),
        ("second", 30.0),
    ]
    result = await engine.dispatch(Event.PRE_TOOL_USE, {"tool_input": {"path": "a.py"}})
    assert result.injected_prompts == ["first", "second"]
    result = await engine.dispatch(
        Event.PRE_TOOL_USE, {"tool_input": {"path": "a.txt"}}
    )
    assert result.injected_prompts == ["second"]
    assert 'hook "first": duplicate name, skipped' in capsys.readouterr().err


@pytest.mark.parametrize(
    ("entry", "error"),
    [
        (hook(""), "name must be a non-empty string"),
        (hook("bad", event="UnknownEvent"), 'unknown event "UnknownEvent"'),
        (hook("bad", action={"type": "unknown"}), "unknown action type"),
        (hook("bad", action={"type": "shell"}), "action.command"),
        (hook("bad", **{"async": True}), "async not allowed for blocking events"),
        (hook("bad", only_once="true"), "only_once must be a boolean"),
        (hook("bad", timeout="0s"), "timeout must be a positive duration"),
        (hook("bad", **{"if": {"any_of": [], "all_of": []}}), "exactly one"),
        (
            hook(
                "bad",
                **{
                    "if": {
                        "all_of": [
                            {
                                "field": "tool_name",
                                "match": {"type": "regex", "value": "["},
                            }
                        ]
                    }
                },
            ),
            "invalid regex",
        ),
        (
            hook(
                "bad",
                **{
                    "if": {"any_of": [{"field": "tool_name", "match": {"type": "not"}}]}
                },
            ),
            "match must be a mapping",
        ),
    ],
)
def test_invalid_entries_do_not_block_good_rule(roots, capsys, entry, error):
    project, _ = roots
    save(project, [entry, hook("good")])
    engine = load(project)
    assert [rule.name for rule in engine.rules] == ["good"]
    assert error in capsys.readouterr().err


@pytest.mark.parametrize("content", ["[malformed", "hooks: {}", ""])
def test_invalid_file_keeps_valid_source(roots, capsys, content):
    project, home = roots
    source = Path(save(project, []))
    source.write_text(content, encoding="utf-8")
    user_source = save(home, [hook("good")])
    engine = load(project)
    assert engine.sources == [user_source]
    assert [rule.name for rule in engine.rules] == ["good"]
    assert str(source) in capsys.readouterr().err


def test_field_path_values():
    assert get_by_path({"a": {"b": False}}, "a.b") == "False"
    assert get_by_path({"a": {"z": 1, "b": 2}}, "a") == '{"b": 2, "z": 1}'
    assert get_by_path({"a": []}, "a.b") == ""
    assert get_by_path({"a": {"b": None}}, "a.b") == ""
