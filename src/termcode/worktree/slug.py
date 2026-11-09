import re
import secrets

EPHEMERAL_PATTERN = re.compile(r"agent-a[0-9a-f]{7}")
_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")


def validate_slug(name: str) -> None:
    if not 1 <= len(name) <= 64:
        raise ValueError("拒绝 Worktree 名称：长度必须为 1 至 64 个字符")
    for segment in name.split("/"):
        if segment in {".", ".."} or not _SEGMENT.fullmatch(segment):
            raise ValueError(f"拒绝 Worktree 名称：无效的路径段 {segment!r}")


def flat_slug(name: str) -> str:
    return name.replace("/", "+")


def random_agent_name() -> str:
    return "agent-a" + secrets.token_hex(4)[:7]
