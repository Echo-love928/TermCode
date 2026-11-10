"""仅清理可识别且位于会话根目录内的过期目录。"""

import asyncio
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from termcode.compact.state import parse_session_time


def _clean(sessions_dir: str, max_age: timedelta, protected: frozenset[str]) -> None:
    root = Path(sessions_dir).resolve()
    if not root.is_dir():
        return
    for directory in root.iterdir():
        try:
            created = parse_session_time(directory.name)
            target = directory.resolve()
            if (
                directory.is_symlink()
                or str(target) in protected
                or not target.is_relative_to(root)
                or not directory.is_dir()
            ):
                continue
            if datetime.now() - created > max_age:
                shutil.rmtree(target)
        except ValueError:
            continue
        except OSError:
            logging.getLogger(__name__).warning("过期会话清理失败：%s", directory.name)


async def clean_expired(
    sessions_dir: str,
    max_age: timedelta = timedelta(days=30),
    *,
    protected: frozenset[str] = frozenset(),
) -> None:
    await asyncio.to_thread(_clean, sessions_dir, max_age, protected)
