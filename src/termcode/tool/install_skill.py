"""由权限引擎授权后下载 Skill，并同步刷新目录和命令。"""

from collections.abc import Callable
from typing import Any

import httpx2

from termcode.skills.install import install_skill, parse_skill_url
from termcode.skills.loader import SkillLoader
from termcode.tool import Result, arguments, string_schema


class InstallSkillTool:
    read_only = False
    category = "write"

    def __init__(self, loader: SkillLoader, on_installed: Callable[[], None]) -> None:
        self._loader = loader
        self._on_installed = on_installed

    def name(self) -> str:
        return "InstallSkill"

    def description(self) -> str:
        return "按用户请求从 skills.sh、GitHub tree 或 raw SKILL.md 链接安装 Skill。"

    def parameters(self) -> dict[str, Any]:
        return string_schema({"url": "用户请求安装的 Skill 链接"})

    async def execute(self, args: str) -> Result:
        try:
            source = parse_skill_url(arguments(args, self.parameters())["url"])
            name = await install_skill(source, self._loader.user_dir)
        except httpx2.HTTPStatusError as exc:
            return Result(
                f"GitHub 请求失败（HTTP {exc.response.status_code}）", is_error=True
            )
        except httpx2.HTTPError:
            return Result("无法连接 GitHub 或请求超时", is_error=True)
        except ValueError as exc:
            return Result(f"安装 Skill 失败：{exc}", is_error=True)
        except OSError:
            return Result("安装 Skill 失败：无法写入用户 Skill 目录", is_error=True)
        self._loader.reload()
        self._on_installed()
        source_label = self._loader.get_source_label(name)
        shadowed = "（当前项目的同名 Skill 优先）" if source_label == "project" else ""
        return Result(f"已安装 Skill '{name}'，可用 /{name} 调用。{shadowed}")
