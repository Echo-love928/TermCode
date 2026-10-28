"""兼容旧式终端和 tmux 的 Alt+Enter 转义序列。"""

import sys
from collections.abc import Iterable

from textual import events
from textual._xterm_parser import XTermParser


class AltEnterParser(XTermParser):
    def _sequence_to_key_events(
        self, sequence: str, alt: bool = False
    ) -> Iterable[events.Key]:
        # 旧式终端使用 ESC + CR；框架默认解析会丢失控制键上的 Alt 修饰。
        if sequence == "\x1b\r" or (sequence == "\r" and alt):
            yield events.Key("alt+enter", None)
            return
        yield from super()._sequence_to_key_events(sequence, alt=alt)


def configure_terminal_keys() -> None:
    if sys.platform == "win32":
        return
    from textual.drivers import linux_inline_driver

    # 仅替换当前 inline 驱动的解析器工厂，不修改已安装的框架文件。
    linux_inline_driver.XTermParser = AltEnterParser  # type: ignore[misc]
