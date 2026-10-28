"""选择终端原生图片组件，并提供不支持图片时的简洁提示。"""

from functools import lru_cache

from PIL import Image as PILImage
from rich.console import Console
from textual import events
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.geometry import Size
from textual.widget import Widget
from textual.widgets import RichLog, Static
from textual_image._terminal import get_cell_size
from textual_image.renderable import Image as RenderableImage
from textual_image.renderable import SixelImage as SixelRenderable
from textual_image.renderable import TGPImage as TGPRenderable
from textual_image.widget import Image, SixelImage, SixelOptions

from termcode.prompt import BANNER_IMAGE


def graphics_available() -> bool:
    # 组件在导入时探测能力；本模块须在 App.run 之前导入，避免争用终端输入。
    return Image is SixelImage or RenderableImage is TGPRenderable


@lru_cache(maxsize=1)
def _image_size() -> tuple[int, int]:
    with PILImage.open(BANNER_IMAGE) as image:
        return image.size


def _fit_width(columns: int, rows: int) -> int:
    cell = get_cell_size()
    width, height = _image_size()
    limit = int(max(2, rows) * cell.height * width / height / cell.width)
    return max(1, min(columns, limit, max(1, width // cell.width)))


def print_banner_image(console: Console) -> None:
    """退出后在普通终端保留同一张原图，不向重定向的日志输出图片控制码。"""
    if not graphics_available() or not console.is_terminal:
        return
    try:
        width = _fit_width(console.width, min(18, console.height - 6))
        if Image is SixelImage:
            image = SixelRenderable(
                BANNER_IMAGE,
                width=width,
                height="auto",
                sixel_options=SixelOptions(quantize="adaptive"),
            )
            console.print(image)
        else:
            console.print(RenderableImage(BANNER_IMAGE, width=width, height="auto"))
    except Exception:
        # 横幅显示失败不能影响消息记录的输出和正常退出。
        return


class BannerArtwork(Widget):
    DEFAULT_CSS = """
    BannerArtwork { width: 1fr; height: auto; }
    BannerArtwork > Image { width: 100%; height: auto; }
    BannerArtwork > Static { height: auto; padding: 0 1; color: $text-muted; }
    """

    def compose(self) -> ComposeResult:
        if graphics_available():
            if Image is SixelImage:
                yield SixelImage(
                    BANNER_IMAGE,
                    id="banner-pixels",
                    sixel_options=SixelOptions(quantize="adaptive"),
                    on_error=self._fallback,
                )
            else:
                yield Image(BANNER_IMAGE, id="banner-pixels", on_error=self._fallback)
        else:
            yield Static("图片横幅需要支持图片的终端，例如 Windows Terminal 1.22+。")

    @staticmethod
    def _fallback(error: Exception) -> Widget:
        return Static("图片横幅暂不可用，文本对话仍可正常使用。")

    def on_mount(self) -> None:
        if self.app.is_headless and not graphics_available():
            self.display = False

    def on_resize(self, event: events.Resize) -> None:
        self.fit_to_window()

    def fit_to_window(self) -> None:
        if not graphics_available() or not self.children:
            return
        try:
            width = _fit_width(
                self.content_size.width, min(18, self.screen.size.height - 12)
            )
        except OSError:
            return
        image = self.query_one("#banner-pixels")
        image.styles.width = width


class TranscriptLog(RichLog):
    """由外层滚动区统一滚动图片和消息，保留 RichLog 的消息渲染行为。"""

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        return max(1, self.virtual_size.height)


class TranscriptViewport(VerticalScroll):
    """内容尺寸真正更新后再跟随消息到底部，避免图片布局的异步时序丢失滚动。"""

    follow_tail = True

    def watch_virtual_size(self, size: Size) -> None:
        if self.follow_tail:
            self.scroll_end(animate=False, immediate=True)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self.follow_tail = False
