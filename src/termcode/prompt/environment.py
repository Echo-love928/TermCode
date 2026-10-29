"""有界采集环境；由调用方在线程中运行，避免阻塞界面。"""

import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Environment:
    working_dir: str
    platform: str
    date: str
    git_status: str
    version: str
    model: str

    def render(self) -> str:
        fields = (
            ("工作目录", self.working_dir),
            ("平台", self.platform),
            ("当前日期", self.date),
            ("git 状态", self.git_status),
            ("应用版本", self.version),
            ("当前模型", self.model),
        )
        return "环境信息：\n" + "\n".join(
            f"{name}: {value}" for name, value in fields if value
        )


def gather_environment(version: str, model: str) -> Environment:
    """只读取允许的环境项；git 不可用、失败或超时均降级。"""
    try:
        working_dir = os.getcwd()
    except OSError:
        working_dir = ""
    try:
        today = date.today().isoformat()
    except (OSError, ValueError, OverflowError):
        today = ""
    git_status = ""
    if working_dir:
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=working_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=2,
                check=False,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().splitlines()
                git_status = "\n".join(lines[:20])[:3000] or "clean"
                if len(lines) > 20:
                    git_status += f"\n…共 {len(lines)} 项变动"
        except (OSError, subprocess.TimeoutExpired):
            pass
    return Environment(working_dir, sys.platform, today, git_status, version, model)
