"""指令优先级与文件展开边界。"""

from termcode.instructions import Loader
from termcode.prompt import build_system_prompt


def put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_priority_missing_and_prompt(tmp_path):
    root, home = tmp_path / "project", tmp_path / "home"
    loader = Loader(str(root), str(home))
    assert loader.load() == ""
    put(root / "TERMCODE.md", "project")
    put(root / ".termcode/TERMCODE.md", "config")
    put(home / ".termcode/TERMCODE.md", "user")
    assert loader.load() == "project\n\nconfig\n\nuser"
    assert loader.load() in build_system_prompt(loader.load(), "记忆测试")
    assert "记忆测试" in build_system_prompt("", "记忆测试")
    assert build_system_prompt() == build_system_prompt("", "")


def test_include_depth_loop_escape_binary(tmp_path):
    root = tmp_path / "project"
    loader = Loader(str(root), str(tmp_path / "home"))
    put(root / "TERMCODE.md", "@include a.md\n内联 @include ignored.md")
    put(root / "a.md", "@include b.md")
    put(root / "b.md", "终点")
    assert "终点" in loader.load()
    put(root / "b.md", "@include TERMCODE.md")
    assert "检测到环路" in loader.load()
    put(root / "b.md", "@include ../outside.md")
    assert "路径超出允许范围" in loader.load()
    (root / "b.md").write_bytes(b"\x00abc")
    assert "二进制" in loader.load()
    put(root / "b.md", "@include c.md")
    put(root / "c.md", "@include d.md")
    put(root / "d.md", "@include e.md")
    put(root / "e.md", "不应加载")
    assert "超过最大嵌套深度" in loader.load()
    assert "不应加载" not in loader.load()
    assert "@include e.md" in loader.load()


def test_sibling_includes_are_not_cycles(tmp_path):
    put(tmp_path / "TERMCODE.md", "@include a.md\n@include a.md")
    put(tmp_path / "a.md", "hello")
    assert Loader(str(tmp_path), str(tmp_path / "home")).load() == "hello\nhello"
