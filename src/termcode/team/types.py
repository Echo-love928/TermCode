from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from .persistence import atomic_json, file_lock, read_json


class BackendType(StrEnum):
    TMUX = "tmux"
    ITERM2 = "iterm2"
    IN_PROCESS = "in-process"


def sanitize(name: str) -> str:
    result = re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip("-")
    if not result or result in {".", ".."}:
        raise ValueError("无效的团队名称")
    return result


@dataclass(frozen=True)
class TeammateInfo:
    name: str
    agent_id: str
    agent_type: str = ""
    model: str = ""
    worktree_path: str = ""
    branch: str = ""
    backend_type: BackendType = BackendType.IN_PROCESS
    pane_id: str = ""
    is_active: bool | None = None
    plan_mode_required: bool = False
    session_dir: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TeammateInfo:
        member = cls(**(data | {"backend_type": BackendType(data["backend_type"])}))
        for value in (member.name, member.agent_id):
            if sanitize(value) != value:
                raise ValueError("无效的队员名称或 ID")
        if any(
            not isinstance(value, str)
            for value in (
                member.agent_type,
                member.model,
                member.worktree_path,
                member.branch,
                member.pane_id,
                member.session_dir,
            )
        ):
            raise ValueError("无效的队员配置")
        if member.is_active is not None and type(member.is_active) is not bool:
            raise ValueError("无效的队员活跃状态")
        if type(member.plan_mode_required) is not bool:
            raise ValueError("无效的队员计划模式")
        return member


@dataclass
class Team:
    name: str
    sanitized_name: str
    description: str
    lead_agent_id: str
    members: list[TeammateInfo]
    config_dir: Path
    created_at: datetime
    backend: BackendType
    project_root: str = ""
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @property
    def config_path(self) -> Path:
        return self.config_dir / "config.json"

    @property
    def mailbox_dir(self) -> Path:
        return self.config_dir / "mailbox"

    @classmethod
    def load(cls, directory: Path) -> Team:
        data = read_json(directory / "config.json")
        if (
            data["sanitized_name"] != directory.name
            or sanitize(directory.name) != directory.name
        ):
            raise ValueError("团队配置目录与名称不一致")
        if not all(
            isinstance(data.get(key, ""), str)
            for key in ("name", "description", "project_root")
        ):
            raise ValueError("无效的团队名称或描述")
        members = [TeammateInfo.from_dict(value) for value in data["members"]]
        lead = data["lead_agent_id"]
        if (
            lead != "lead"
            or not any(m.agent_id == lead and m.name == "lead" for m in members)
            or len({m.name.casefold() for m in members}) != len(members)
            or len({m.agent_id for m in members}) != len(members)
        ):
            raise ValueError("无效的团队成员列表")
        return cls(
            data["name"],
            directory.name,
            data.get("description", ""),
            lead,
            members,
            directory,
            datetime.fromtimestamp(data["created_at"], UTC),
            BackendType(data["backend"]),
            data.get("project_root", ""),
        )

    def save(self, members: list[TeammateInfo]) -> None:
        atomic_json(
            self.config_path,
            {
                "name": self.name,
                "sanitized_name": self.sanitized_name,
                "description": self.description,
                "lead_agent_id": self.lead_agent_id,
                "backend": self.backend,
                "project_root": self.project_root,
                "created_at": self.created_at.timestamp(),
                "members": [asdict(member) for member in members],
            },
        )
        self.members = members

    def refresh(self) -> None:
        self.members = Team.load(self.config_dir).members

    def member(self, target: str) -> TeammateInfo | None:
        return next((m for m in self.members if m.name == target), None) or next(
            (m for m in self.members if m.agent_id == target), None
        )

    async def _mutate(
        self, update: Callable[[list[TeammateInfo]], list[TeammateInfo]]
    ) -> None:
        async with self._lock, file_lock(self.config_dir / "config.lock"):
            self.refresh()
            members = update(self.members)
            if members != self.members:
                self.save(members)

    async def add_member(self, info: TeammateInfo) -> None:
        def add(members: list[TeammateInfo]) -> list[TeammateInfo]:
            if any(m.name.casefold() == info.name.casefold() for m in members):
                raise ValueError(f"团队已有队员 {info.name}")
            return [*members, info]

        await self._mutate(add)

    async def update_member(self, agent_id: str, **changes: Any) -> None:
        await self._mutate(
            lambda members: [
                replace(member, **changes) if member.agent_id == agent_id else member
                for member in members
            ]
        )

    async def set_member_active(self, name: str, active: bool) -> None:
        await self._mutate(
            lambda members: [
                replace(member, is_active=active) if member.name == name else member
                for member in members
            ]
        )

    async def remove_member(self, name: str) -> None:
        if name == "lead":
            raise ValueError("不能移除团队负责人")
        await self._mutate(lambda members: [m for m in members if m.name != name])
