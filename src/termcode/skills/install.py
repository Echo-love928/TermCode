"""通过 GitHub Contents API 下载并原子安装 Skill 目录。"""

import asyncio
import base64
import json
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import httpx2

from termcode.skills.parser import SKILL_NAME, parse_frontmatter, parse_skill_file

MAX_FILE_SIZE = 1024 * 1024
MAX_TOTAL_SIZE = 8 * MAX_FILE_SIZE
MAX_FILE_COUNT = 64
MAX_RECURSION_DEPTH = 4
MAX_RESPONSE_SIZE = 2 * MAX_FILE_SIZE
MAX_DISCOVERY_DIRS = 24
GITHUB_ID = re.compile(r"[A-Za-z0-9_.-]+")
WINDOWS_DEVICE = re.compile(r"(?i:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])")


@dataclass(frozen=True)
class SkillSource:
    owner: str
    repo: str
    ref: str | None = None
    path: tuple[str, ...] = ()
    slug: str | None = None


def _safe_name(name: object) -> str:
    if (
        not isinstance(name, str)
        or not name
        or name.casefold() == ".git"
        or name.endswith((".", " "))
        or any(ord(c) < 32 or c in '/\\:<>"|?*' for c in name)
        or WINDOWS_DEVICE.fullmatch(name.split(".", 1)[0])
    ):
        raise ValueError("Skill 路径包含无效的文件名")
    return name


def parse_skill_url(url: str) -> SkillSource:
    parsed = urlsplit(url)
    host = parsed.netloc.lower()
    if parsed.scheme != "https" or host not in {
        "github.com",
        "raw.githubusercontent.com",
        "skills.sh",
        "www.skills.sh",
    }:
        raise ValueError("仅支持 HTTPS 的 skills.sh 和 GitHub Skill 链接")
    parts = [
        unquote(part, errors="strict") for part in parsed.path.strip("/").split("/")
    ]
    if len(parts) < 3 or any(not GITHUB_ID.fullmatch(p) for p in parts[:2]):
        raise ValueError("链接缺少有效的 GitHub owner 和 repo")
    owner, repo, *rest = parts
    _safe_name(owner)
    _safe_name(repo)
    if host in {"skills.sh", "www.skills.sh"}:
        if len(rest) != 1 or not SKILL_NAME.fullmatch(rest[0]):
            raise ValueError("需要 skills.sh/<owner>/<repo>/<skill> 链接")
        return SkillSource(owner, repo, slug=rest[0])
    if host == "github.com":
        if len(rest) < 2 or rest[0] != "tree":
            raise ValueError("需要 GitHub tree 目录链接")
        _, ref, *path = rest
    else:
        if len(rest) >= 4 and rest[:2] in (["refs", "heads"], ["refs", "tags"]):
            ref, path = "/".join(rest[:3]), rest[3:]
        else:
            ref, *path = rest
        if not path or path.pop() != "SKILL.md":
            raise ValueError("raw.githubusercontent.com 链接必须指向 SKILL.md")
    for part in ref.split("/"):
        _safe_name(part)
    return SkillSource(owner, repo, ref, tuple(_safe_name(part) for part in path))


class _Contents:
    def __init__(self, client: httpx2.AsyncClient, source: SkillSource) -> None:
        self.client = client
        self.source = source
        self.files = 0
        self.total = 0

    async def get(self, path: tuple[str, ...]) -> Any:
        src = self.source
        url = f"https://api.github.com/repos/{src.owner}/{src.repo}/contents"
        if path:
            url += "/" + "/".join(quote(part, safe="") for part in path)
        async with self.client.stream(
            "GET",
            url,
            params={"ref": src.ref} if src.ref else None,
            headers={"Accept": "application/vnd.github+json"},
        ) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=65536):
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_SIZE:
                    raise ValueError("GitHub 响应超出大小限制")
        return json.loads(body)

    @staticmethod
    def entries(data: Any) -> list[dict[str, Any]]:
        if not isinstance(data, list) or not all(
            isinstance(item, dict) for item in data
        ):
            raise ValueError("链接未指向有效的 GitHub 目录")
        if len(data) >= 1000:
            raise ValueError("GitHub 目录过大，请提供 Skill 的直接目录链接")
        return data

    async def resolve(self) -> tuple[tuple[str, ...], list[dict[str, Any]]]:
        slug = self.source.slug
        if slug is None:
            path = self.source.path
            return path, self.entries(await self.get(path))
        # 先检查常用路径，再在有限范围内找同名目录。
        queue = deque([("skills", slug), (slug,), (".agents", "skills", slug), ()])
        seen: set[tuple[str, ...]] = set()
        while queue and len(seen) < MAX_DISCOVERY_DIRS:
            path = queue.popleft()
            if path in seen:
                continue
            seen.add(path)
            try:
                entries = self.entries(await self.get(path))
            except httpx2.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    continue
                raise
            if any(
                e.get("name") == "SKILL.md" and e.get("type") == "file" for e in entries
            ):
                if path and path[-1] == slug:
                    return path, entries
                if not path:
                    raw = await self.read_file(("SKILL.md",))
                    if parse_frontmatter(raw.decode("utf-8"))[0].get("name") == slug:
                        return path, entries
            if len(path) >= MAX_RECURSION_DEPTH:
                continue
            dirs = [
                path + (_safe_name(e.get("name")),)
                for e in entries
                if e.get("type") == "dir"
            ]
            queue.extendleft(reversed([p for p in dirs if p[-1] == slug]))
            queue.extend(
                p
                for p in dirs
                if p[-1] != slug
                and (
                    not p[-1].startswith(".")
                    or p[-1] in {".agents", ".claude", ".github"}
                )
            )
        raise ValueError("找不到对应 Skill，请提供 GitHub tree 或 raw 链接")

    async def read_file(self, path: tuple[str, ...]) -> bytes:
        data = await self.get(path)
        if (
            not isinstance(data, dict)
            or data.get("type") != "file"
            or data.get("encoding") != "base64"
            or not isinstance(data.get("content"), str)
        ):
            raise ValueError("无法读取 GitHub 普通文件")
        decoded = base64.b64decode("".join(data["content"].split()), validate=True)
        if len(decoded) > MAX_FILE_SIZE:
            raise ValueError("Skill 单文件超出 1 MiB")
        return decoded

    async def download(
        self,
        path: tuple[str, ...],
        entries: list[dict[str, Any]],
        target: Path,
        depth: int = 0,
    ) -> None:
        seen: set[str] = set()
        for entry in entries:
            name = _safe_name(entry.get("name"))
            key = name.casefold()
            if key in seen:
                raise ValueError("Skill 目录包含重复文件名")
            seen.add(key)
            remote, local = path + (name,), target / name
            match entry.get("type"):
                case "dir":
                    if depth >= MAX_RECURSION_DEPTH:
                        raise ValueError("Skill 目录深度超出 4 层")
                    children = self.entries(await self.get(remote))
                    local.mkdir()
                    await self.download(remote, children, local, depth + 1)
                case "file":
                    self.files += 1
                    if self.files > MAX_FILE_COUNT:
                        raise ValueError("Skill 文件数超出 64")
                    size = entry.get("size")
                    if not isinstance(size, int) or not 0 <= size <= MAX_FILE_SIZE:
                        raise ValueError("Skill 单文件大小无效或超出 1 MiB")
                    if self.total + size > MAX_TOTAL_SIZE:
                        raise ValueError("Skill 总大小超出 8 MiB")
                    data = await self.read_file(remote)
                    self.total += len(data)
                    if self.total > MAX_TOTAL_SIZE:
                        raise ValueError("Skill 总大小超出 8 MiB")
                    with local.open("xb") as output:
                        output.write(data)
                case _:
                    raise ValueError("Skill 只能包含普通文件和目录")


def _publish(src: SkillSource, staging: Path, install_root: Path) -> str:
    skill = parse_skill_file(staging / "SKILL.md")
    if src.slug and src.slug != skill.name:
        raise ValueError("SKILL.md 的 name 与 skills.sh 链接不一致")
    destination = install_root / _safe_name(skill.name)
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"Skill 已存在：{skill.name}")
    staging.rename(destination)
    return skill.name


async def install_skill(src: SkillSource, install_root: Path) -> str:
    await asyncio.to_thread(install_root.mkdir, parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".install-", dir=install_root) as staging:
        temporary = Path(staging)
        async with httpx2.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            contents = _Contents(client, src)
            path, entries = await contents.resolve()
            if not any(
                e.get("name") == "SKILL.md" and e.get("type") == "file" for e in entries
            ):
                raise ValueError("Skill 目录缺少 SKILL.md")
            await contents.download(path, entries, temporary)
        # 下载连接已关闭。发布到刷新目录之间不让出事件循环。
        return _publish(src, temporary, install_root)
