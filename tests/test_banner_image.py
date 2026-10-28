"""验证原图横幅与消息共同滚动，不遮挡输入，也不依赖云端服务。"""

import asyncio

import pytest
from rich.text import Text
from textual.containers import VerticalScroll
from textual_image.widget import SixelImage

from termcode.config import ProviderConfig
from termcode.tui import banner
from termcode.tui.app import ChatInput, SessionState, TermCodeApp


def image_app() -> TermCodeApp:
    return TermCodeApp(
        [
            ProviderConfig(
                "preview", "openai", "preview-placeholder-key", "preview-model"
            )
        ]
    )


@pytest.mark.parametrize("columns", [100, 180])
async def test_native_image_scrolls_with_history_and_resizes(monkeypatch, columns):
    monkeypatch.setattr(banner, "Image", SixelImage)
    app = image_app()
    async with app.run_test(size=(columns, 36)) as pilot:
        await pilot.pause()
        artwork = app.query_one("#startup-artwork", banner.BannerArtwork)
        scroll = app.query_one("#transcript", VerticalScroll)
        assert artwork.region.width > 0 and artwork.region.height > 0
        assert artwork.query_one(SixelImage).region.height > 0
        assert artwork.query_one(SixelImage).region.height <= 18
        assert app.query_one("#input", ChatInput).has_focus
        for index in range(45):
            app._write(Text(f"历史消息 {index}"))
        async with asyncio.timeout(5):
            while scroll.scroll_y <= 0:
                await pilot.pause(0.05)
        assert scroll.scroll_y > 0
        assert artwork.region.y < scroll.content_region.y
        await pilot.press("pageup", "pageup", "pageup")
        async with asyncio.timeout(5):
            while scroll.scroll_y > 0:
                await pilot.pause(0.05)
        await pilot.pause()
        assert artwork.region.y == scroll.content_region.y
        await pilot.resize_terminal(38, 20)
        await pilot.pause()
        assert artwork.region.width <= 38
        assert app.query_one("#composer").region.bottom <= 20
        assert app.query_one("#input", ChatInput).read_only is False
        assert app.state == SessionState.IDLE


async def test_text_terminal_remains_usable_without_graphics(monkeypatch):
    monkeypatch.setattr(banner, "graphics_available", lambda: False)
    app = image_app()
    async with app.run_test(size=(80, 32)):
        assert app.query_one("#startup-artwork").display is False
        assert app.query_one("#input", ChatInput).has_focus
        assert app.state == SessionState.IDLE
