"""团队配置、任务和邮箱共用的短时文件锁和原子写入。"""

import asyncio
import json
import os
import random
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any


@asynccontextmanager
async def file_lock(path: Path) -> AsyncIterator[None]:
    for attempt in range(10):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            try:
                if time.time() - os.stat(path).st_mtime > 10:
                    os.unlink(path)
                    continue
            except FileNotFoundError:
                continue
            if attempt < 9:
                await asyncio.sleep(random.uniform(0.005, 0.1))
    else:
        raise TimeoutError(f"无法取得团队文件锁：{path.name}")
    os.close(fd)
    try:
        # 持锁期间只执行短暂的同步读写，不运行 Agent 或子进程。
        yield
    finally:
        with suppress(FileNotFoundError):
            os.unlink(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: object) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(name, path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(name)
