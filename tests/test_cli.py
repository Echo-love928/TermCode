"""真实子进程入口的配置失败与错误脱敏。"""

import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_cli_help_without_configuration(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-m", "termcode", "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert b"--version" in result.stdout


@pytest.mark.parametrize(
    "content,expected",
    [
        (None, "配置文件不存在"),
        ("providers: [TEST_SECRET", "YAML 格式错误"),
        ("providers: []", "非空列表"),
        (
            "providers:\n  - name: test\n    protocol: openai\n    model: test",
            "api_key",
        ),
        (
            "providers:\n  - name: test\n    protocol: bad\n"
            "    model: test\n    api_key: TEST_SECRET",
            "protocol",
        ),
    ],
)
def test_cli_reports_config_error_without_traceback(tmp_path, content, expected):
    if content is not None:
        directory = tmp_path / ".termcode"
        directory.mkdir()
        (directory / "config.yaml").write_text(content, encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, "-m", "termcode"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 1
    assert expected in result.stderr
    assert "Traceback" not in result.stderr
    assert "TEST_SECRET" not in result.stdout + result.stderr
