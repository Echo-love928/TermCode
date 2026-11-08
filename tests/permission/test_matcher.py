"""验证权限匹配语法、兼容性与逐条错误降级。"""

import pytest

from termcode.permission import Decision
from termcode.permission.matcher import compile_matcher
from termcode.permission.rule import escape_glob, parse_rule
from termcode.permission.settings import load_settings, to_rule_set


@pytest.mark.parametrize(
    ("pattern", "command", "target", "expected"),
    [
        ("=git status", True, "git status", True),
        ("=git status", True, "git status -s", False),
        ("=", False, "", True),
        ("~^npm (install|test)$", True, "npm install", True),
        ("~^npm (install|test)$", True, "npm run dev", False),
        ("~(?i)delete", True, "please DELETE it", True),
        ("!=foo", True, "foo", False),
        ("!!=foo", True, "foo", True),
        ("!~^rm", True, "ls -lh", True),
        ("!~^rm", True, "rm -rf .", False),
        ("!git *", True, "git status", False),
        ("git *", True, "git add dir/file", True),
        ("*.py", False, "src/main.py", False),
        ("**/*.py", False, "main.py", True),
        ("**/*.py", False, "src/main.py", True),
        (r"file\*.py", False, "file*.py", True),
    ],
)
def test_matcher(pattern, command, target, expected):
    assert compile_matcher(pattern, is_command=command).match(target) is expected


@pytest.mark.parametrize("pattern", ["", "!", "~[invalid"])
def test_invalid_matcher(pattern):
    with pytest.raises(ValueError):
        compile_matcher(pattern, is_command=True)


@pytest.mark.parametrize("target", ["=a", "!a", "~a", "a*(b)", r"a\b"])
def test_generated_allow_glob_is_literal(target):
    rule, error = parse_rule(f"Bash({escape_glob(target)})")
    assert error is None
    assert rule is not None and rule.matcher is not None
    assert rule.matcher.match(target)
    assert not rule.matcher.match(target + "x")


def test_rule_set_loads_remaining_rules(tmp_path, capsys):
    path = tmp_path / "settings.yaml"
    path.write_text(
        "permissions:\n"
        '  allow: [42, "Bash(~[)", "Bash(=git status)", "Write(**/*.py)"]\n'
        '  deny: ["Bash(!~^git)"]\n',
        encoding="utf-8",
    )
    rules = to_rule_set(load_settings(str(path)))
    assert rules.match("Bash", "git status") == (Decision.ALLOW, True)
    assert rules.match("Bash", "git status -s") == (Decision.ALLOW, False)
    assert rules.match("Bash", "ls -lh") == (Decision.DENY, True)
    assert rules.match("Write", "src/main.py") == (Decision.ALLOW, True)
    stderr = capsys.readouterr().err
    assert "rule 'Bash(~[)' parse failed: invalid regex" in stderr
    assert "rule 42 parse failed: expected a string" in stderr
