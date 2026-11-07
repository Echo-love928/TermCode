"""注册中心的冲突原子性、隐藏命令和匹配边界。"""

import pytest

from termcode.command import Command, Kind, Registry


async def noop(ui):
    pass


def command(name, **kwargs):
    return Command(name, "不是匹配依据", Kind.LOCAL, noop, **kwargs)


def test_register_ok():
    reg = Registry()
    cmd = command("help", aliases=["h"])
    reg.register(cmd)
    assert reg.lookup("HELP") is cmd
    assert reg.lookup("h") is cmd
    assert reg.lookup("missing") is None


@pytest.mark.parametrize("alias", [False, True])
def test_register_duplicate_name_raises(alias):
    reg = Registry()
    reg.register(command("help", aliases=["h"]))
    key = "h" if alias else "help"
    with pytest.raises(RuntimeError, match=key):
        reg.register(command(key))


@pytest.mark.parametrize(
    ("aliases", "key"),
    [(["help"], "help"), (["new", "h"], "h"), (["same", "same"], "same")],
)
def test_register_duplicate_alias_raises(aliases, key):
    reg = Registry()
    reg.register(command("help", aliases=["h"]))
    with pytest.raises(RuntimeError, match=rf"command conflict: {key}$"):
        reg.register(command("other", aliases=aliases))
    assert reg.lookup("other") is None
    assert reg.lookup("new") is None
    assert reg.lookup("same") is None


@pytest.mark.parametrize("name", ["", "Help", "/help", "two words", "x\ny"])
def test_invalid_name_is_rejected(name):
    with pytest.raises(ValueError):
        Registry().register(command(name))


def test_visible_sorted_and_hidden_dispatchable():
    reg = Registry()
    for name in ("status", "help", "clear"):
        reg.register(command(name))
    hidden = command("secret", hidden=True)
    reg.register(hidden)
    assert [c.name for c in reg.visible()] == ["clear", "help", "status"]
    assert reg.lookup("SECRET") is hidden
    visible = reg.visible()
    visible.clear()
    assert len(reg.visible()) == 3


def test_prefix_match():
    reg = Registry()
    for cmd in [
        command("session"),
        command("status"),
        command("help", aliases=["sh"]),
        command("secret", hidden=True),
    ]:
        reg.register(cmd)
    assert [c.name for c in reg.prefix_match("/S")] == ["session", "status"]
    assert len(reg.prefix_match("/")) == 3
    assert reg.prefix_match("/sh") == []
    assert reg.prefix_match("/不是") == []
    assert reg.prefix_match("//s") == []
