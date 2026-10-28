"""按真实终端字节序列验证键盘兼容，而非仅模拟框架按键事件。"""

import pytest

from termcode.tui.keys import AltEnterParser


@pytest.mark.parametrize(
    "sequence,expected",
    [("\x1b\r", "alt+enter"), ("\x1b[13;3u", "alt+enter"), ("\r", "enter")],
)
def test_legacy_and_extended_enter_sequences(sequence, expected):
    events = list(AltEnterParser().feed(sequence))
    assert [event.key for event in events] == [expected]
