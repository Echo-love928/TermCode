from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class MessageType(StrEnum):
    TEXT = "text"
    SHUTDOWN_REQUEST = "shutdown_request"
    SHUTDOWN_RESPONSE = "shutdown_response"
    PLAN_APPROVAL_REQUEST = "plan_approval_request"
    PLAN_APPROVAL_RESPONSE = "plan_approval_response"


@dataclass(frozen=True)
class Message:
    from_: str
    text: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    read: bool = False
    type: MessageType = MessageType.TEXT
    request_id: str = ""
    approve: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "from": self.from_,
            "text": self.text,
            "timestamp": self.timestamp,
            "read": self.read,
        }
        if self.type != MessageType.TEXT:
            data["type"] = self.type
        if self.request_id:
            data["requestId"] = self.request_id
        if self.approve is not None:
            data["approve"] = self.approve
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        return cls(
            data["from"],
            data["text"],
            data["timestamp"],
            data["read"],
            MessageType(data.get("type", "text")),
            data.get("requestId", ""),
            data.get("approve"),
        )
