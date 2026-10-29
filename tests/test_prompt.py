"""验证模块扩展、稳定前缀与环境降级。"""

import os
import subprocess
import sys
from datetime import date
from types import SimpleNamespace

import pytest

from termcode.prompt import (
    BANNER_IMAGE,
    Environment,
    Module,
    assemble_system,
    build_system_prompt,
    fixed_modules,
    gather_environment,
    optional_modules,
    plan_reminder,
    system_reminder,
)
from termcode.tool import new_default_registry


def test_module_order_extension_and_empty_slots():
    modules = fixed_modules()
    assert [m.name for m in modules] == [
        "身份",
        "系统约束",
        "任务模式",
        "动作执行",
        "工具使用",
        "语气风格",
        "文本输出",
    ]
    assert [m.priority for m in modules] == list(range(10, 80, 10))
    assert [m.name for m in optional_modules()] == [
        "自定义指令",
        "已激活 Skill",
        "长期记忆",
    ]
    assert all(not m.content for m in optional_modules())
    assert build_system_prompt() == "\n\n".join(m.content for m in modules)
    extra = Module("扩展", 45, "扩展正文")
    result = assemble_system(list(reversed(modules)) + [extra] + optional_modules())
    assert result.index(modules[3].content) < result.index(extra.content)
    assert result.index(extra.content) < result.index(modules[4].content)
    assert "\n\n\n" not in result
    assert assemble_system([Module("空", 1, ""), Module("正文", 2, "正文")]) == "正文"


def test_stable_prompt_independent_of_environment(monkeypatch, tmp_path):
    before = build_system_prompt().encode()
    # Windows 测试临时目录可能在仓库内，阻止 git 向父目录发现项目仓库。
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    monkeypatch.chdir(tmp_path)
    env = gather_environment("test-version", "test-model")
    assert env.working_dir == str(tmp_path)
    assert env.platform == sys.platform
    assert env.date == date.today().isoformat()
    assert env.git_status == ""
    assert build_system_prompt().encode() == before
    for dynamic in (str(tmp_path), env.date, "test-version", "test-model"):
        assert dynamic not in before.decode()
    assert BANNER_IMAGE.is_file()


def test_environment_render_and_no_environment_variable_leak(monkeypatch):
    monkeypatch.setenv("API_KEY", "SECRET_SENTINEL")
    env = Environment("/project", "linux", "2026-10-29", " M x.py", "dev", "model")
    rendered = env.render()
    for value in ("/project", "linux", "2026-10-29", " M x.py", "dev", "model"):
        assert value in rendered
    assert "SECRET_SENTINEL" not in rendered
    assert "git 状态" not in Environment("/tmp", "linux", "", "", "", "").render()


@pytest.mark.parametrize("failure", ["missing", "timeout", "not-git"])
def test_git_failure_is_bounded_and_degrades(monkeypatch, failure):
    def run(args, **kwargs):
        assert args == ["git", "status", "--porcelain"]
        assert kwargs["timeout"] == 2
        assert not kwargs.get("shell", False)
        if failure == "missing":
            raise FileNotFoundError
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 2)
        return SimpleNamespace(returncode=128, stdout="", stderr="not a repository")

    monkeypatch.setattr(subprocess, "run", run)
    assert gather_environment("dev", "model").git_status == ""


def test_missing_cwd_degrades(monkeypatch):
    def missing():
        raise OSError("directory removed")

    monkeypatch.setattr(os, "getcwd", missing)
    assert gather_environment("dev", "model").working_dir == ""


def test_git_status_summary_is_bounded(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=" M changed.py\n" * 100),
    )
    status = gather_environment("dev", "model").git_status
    assert status.count("changed.py") == 20 and "100 项变动" in status


def test_double_reinforcement_and_reminder_semantics():
    stable = build_system_prompt()
    tools = new_default_registry()
    assert "编辑文件前必须先用 read_file" in stable
    for text in (stable, tools.get("bash").description()):
        assert "优先" in text
        assert all(name in text for name in ("read_file", "glob", "grep"))
    assert "先用 read_file" in tools.get("edit_file").description()
    assert "系统补充上下文" in stable and "不要将它当作用户提问" in stable
    full, short = plan_reminder(True), plan_reminder(False)
    for reminder in (full, short):
        assert reminder.startswith("<system-reminder>\n")
        assert reminder.endswith("\n</system-reminder>")
        assert "只读工具" in reminder and "/do" in reminder
    assert len(short) < len(full)
    assert "分步计划" in full
    assert system_reminder("") == ""
