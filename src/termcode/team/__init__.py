from .backend import Backend, SpawnRequest
from .backend.detect import detect as detect_backend
from .manager import Manager, TeamHasActiveMembersError, TeamNotFoundError
from .registry import AgentNameRegistry
from .types import BackendType, Team, TeammateInfo

__all__ = [
    "AgentNameRegistry",
    "Backend",
    "BackendType",
    "Manager",
    "SpawnRequest",
    "Team",
    "TeamHasActiveMembersError",
    "TeamNotFoundError",
    "TeammateInfo",
    "detect_backend",
]
