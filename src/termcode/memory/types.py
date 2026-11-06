"""笔记类型与模型更新操作。"""

from dataclasses import dataclass
from enum import StrEnum


class NoteType(StrEnum):
    USER_PREFERENCE = "user_preference"
    CORRECTION_FEEDBACK = "correction_feedback"
    PROJECT_KNOWLEDGE = "project_knowledge"
    REFERENCE_MATERIAL = "reference_material"


@dataclass
class Note:
    type: NoteType
    title: str
    content: str
    created: str = ""
    updated: str = ""


@dataclass
class UpdateAction:
    action: str
    level: str = "project"
    type: str = ""
    title: str = ""
    slug: str = ""
    content: str = ""
    filename: str = ""
