from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_cwd: ContextVar[str | None] = ContextVar("cwd", default=None)


@contextmanager
def with_cwd(directory: str) -> Iterator[None]:
    if not directory:
        yield
        return
    token = _cwd.set(directory)
    try:
        yield
    finally:
        _cwd.reset(token)


def cwd_from_ctx() -> str | None:
    return _cwd.get()


def resolve_path(p: str) -> str:
    if Path(p).is_absolute():
        return p
    return str(Path(cwd_from_ctx() or Path.cwd()) / p)
