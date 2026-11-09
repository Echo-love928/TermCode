"""命令仅依赖此协议；空实现供无界面的调用和测试使用。"""

from dataclasses import dataclass
from typing import Protocol

from termcode.hook.rule import Rule
from termcode.permission import Mode
from termcode.skills.parser import SkillDef


@dataclass(frozen=True)
class WorktreeSummary:
    name: str
    path: str
    branch: str
    active: bool
    manual: bool


class WorktreeAccessor(Protocol):
    async def create(self, name: str) -> tuple[str, str]: ...
    def list(self) -> list[WorktreeSummary]: ...
    async def enter(self, name: str) -> str: ...
    async def exit(self, action: str, discard: bool) -> bool: ...
    async def remove(self, name: str, discard: bool) -> None: ...


class UI(Protocol):
    def println(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...
    def mode(self) -> Mode: ...
    def set_mode(self, mode: Mode) -> None: ...
    def inject_and_send(self, display_label: str, preset_prompt: str) -> None: ...
    def usage_in(self) -> int: ...
    def usage_out(self) -> int: ...
    def model_name(self) -> str: ...
    def cwd(self) -> str: ...
    def tool_count(self) -> int: ...
    def memory_files(self) -> list[str]: ...
    def hook_sources(self) -> list[str]: ...
    def hook_rules(self) -> list[Rule]: ...
    def session_path(self) -> str: ...
    def session_id(self) -> str: ...
    def quit(self) -> None: ...
    def force_compact(self) -> None: ...
    def open_resume_menu(self) -> None: ...
    async def clear_and_new_session(self) -> None: ...
    def run_skill(self, skill: SkillDef, args: str) -> None: ...
    def idle(self) -> bool: ...
    def worktree_accessor(self) -> WorktreeAccessor | None: ...


class NopUI:
    def println(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass

    def mode(self) -> Mode:
        return Mode.DEFAULT

    def set_mode(self, mode: Mode) -> None:
        pass

    def inject_and_send(self, display_label: str, preset_prompt: str) -> None:
        pass

    def usage_in(self) -> int:
        return 0

    def usage_out(self) -> int:
        return 0

    def model_name(self) -> str:
        return ""

    def cwd(self) -> str:
        return ""

    def tool_count(self) -> int:
        return 0

    def memory_files(self) -> list[str]:
        return []

    def hook_sources(self) -> list[str]:
        return []

    def hook_rules(self) -> list[Rule]:
        return []

    def session_path(self) -> str:
        return ""

    def session_id(self) -> str:
        return ""

    def quit(self) -> None:
        pass

    def force_compact(self) -> None:
        pass

    def open_resume_menu(self) -> None:
        pass

    async def clear_and_new_session(self) -> None:
        pass

    def run_skill(self, skill: SkillDef, args: str) -> None:
        pass

    def idle(self) -> bool:
        return True

    def worktree_accessor(self) -> WorktreeAccessor | None:
        return None
