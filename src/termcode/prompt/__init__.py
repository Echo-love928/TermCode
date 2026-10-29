"""模块化系统提示、环境上下文与原图横幅的文字信息。"""

from dataclasses import dataclass
from pathlib import Path

from rich.console import Console, ConsoleOptions, RenderResult
from rich.text import Text

from termcode.prompt.environment import Environment, gather_environment
from termcode.prompt.modules import Module, fixed_modules, optional_modules
from termcode.prompt.reminder import (
    EXECUTE_DIRECTIVE,
    plan_reminder,
    system_reminder,
)

__all__ = [
    "EXECUTE_DIRECTIVE",
    "Environment",
    "Module",
    "assemble_system",
    "build_system_prompt",
    "fixed_modules",
    "gather_environment",
    "optional_modules",
    "plan_reminder",
    "system_reminder",
    "BANNER_IMAGE",
    "StartupBanner",
    "render_banner",
]


def assemble_system(mods: list[Module]) -> str:
    """按优先级拼装，忽略空模块且不制造额外空段。"""
    return "\n\n".join(
        mod.content.strip()
        for mod in sorted(mods, key=lambda mod: mod.priority)
        if mod.content.strip()
    )


def build_system_prompt() -> str:
    return assemble_system(fixed_modules() + optional_modules())


BANNER_IMAGE = Path(__file__).resolve().parent.parent / "assets" / "startup-banner.png"
_READY_TEXT = (
    "就绪 · Enter 发送 · Alt+Enter 换行 · Esc 取消 · /plan 计划 · /do 执行 · /exit 退出"
)


@dataclass(frozen=True)
class StartupBanner:
    """图片交由终端图片组件显示，文字信息仍由 Rich 安全渲染。"""

    version: str
    cwd: str
    show_name: bool = True

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        title = f"TermCode v{self.version}" if self.show_name else f"v{self.version}"
        yield Text(title, style="bold #ffae4a")
        yield Text(self.cwd)
        yield Text("")
        yield Text(_READY_TEXT)


def render_banner(version: str, cwd: str) -> str:
    return f"TermCode v{version}\n{cwd}\n\n{_READY_TEXT}"
