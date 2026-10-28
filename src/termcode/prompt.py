"""内置系统提示词与原图横幅的文字信息。"""

from dataclasses import dataclass
from pathlib import Path

from rich.console import Console, ConsoleOptions, RenderResult
from rich.text import Text

SYSTEM_PROMPT = (
    "你是 TermCode，一个运行在终端中的 AI 编程对话助手。"
    "请清晰、准确地回答问题，默认使用中文，代码示例使用 Markdown 代码块。"
    "你目前只支持文本对话，不能调用工具、读取文件或修改代码；"
    "不要声称执行过未实际执行的操作。"
)

BANNER_IMAGE = Path(__file__).with_name("assets") / "startup-banner.png"
_READY_TEXT = "就绪 · Enter 发送 · Alt+Enter 换行 · /exit 或 Ctrl+C 退出"


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
