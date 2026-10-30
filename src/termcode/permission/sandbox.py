"""解析真实路径与最近祖先后判断单一项目根边界。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from termcode.permission.engine import Engine


def resolve_root(root: str) -> str:
    path = Path(root).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise NotADirectoryError(root)
    return str(path)


def eval_symlinks_or_ancestor(abs_path: str) -> str:
    path = Path(abs_path)
    suffix: list[str] = []
    while True:
        try:
            resolved = path.resolve(strict=True)
            break
        except FileNotFoundError:
            # 悬空链接不能当作尚未创建的普通目录放行。
            if path.is_symlink() or path == path.parent:
                raise
            suffix.insert(0, path.name)
            path = path.parent
    return str(resolved.joinpath(*suffix).resolve(strict=False))


def sandbox_ok(engine: Engine, path: str) -> bool:
    if not engine.root_valid:
        return False
    try:
        target = Path(path).expanduser() if path else Path(engine.root)
        if not target.is_absolute():
            target = Path(engine.root) / target
        resolved = os.path.normcase(eval_symlinks_or_ancestor(str(target)))
        root = os.path.normcase(engine.root)
        return os.path.commonpath((root, resolved)) == root
    except (OSError, RuntimeError, ValueError):
        return False
