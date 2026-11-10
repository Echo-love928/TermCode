"""补全过滤、滚动、高亮和多行输入边界。"""

import pytest

from termcode.command import Registry, register_builtins
from termcode.tui.complete import MAX_ROWS, CompletionMenu


def registry():
    reg = Registry()
    register_builtins(reg)
    return reg


def test_filter_scroll_and_render():
    menu = CompletionMenu()
    reg = registry()
    menu.update("/", reg)
    assert menu.active and len(menu.items) == 15
    assert len(menu.render(80).plain.splitlines()) <= MAX_ROWS
    for _ in range(20):
        menu.move_down()
    assert menu.selected().name == "worktree"
    assert menu.offset == len(menu.items) - MAX_ROWS
    assert "/status" in menu.render(80).plain
    assert menu.render(80).spans
    menu.move_up()
    assert menu.selected().name == "team"
    menu.move_up()
    assert menu.selected().name == "status"
    menu.move_up()
    assert menu.selected().name == "session"
    menu.update("/S", reg)
    assert [c.name for c in menu.items] == ["session", "status"]
    assert menu.selected().name == "session"
    assert menu.offset == 0
    for _ in range(20):
        menu.move_up()
    assert menu.cursor == 0
    menu.update("/foobar", reg)
    assert menu.active and menu.selected() is None
    assert menu.render(80).plain == "无匹配"
    menu.move_up()
    menu.move_down()
    assert menu.cursor == 0


@pytest.mark.parametrize("value", ["", "text", " /s", "/s\n", "/s\ntext", "/s\rtext"])
def test_completion_deactivates_for_non_slash_or_multiline(value):
    menu = CompletionMenu()
    menu.update("/", registry())
    menu.update(value, registry())
    assert not menu.active and not menu.items
    assert menu.render(80).plain == ""


def test_render_respects_terminal_cell_width():
    menu = CompletionMenu()
    menu.update("/", registry())
    assert all(line.cell_len <= 8 for line in menu.render(8).split("\n"))
