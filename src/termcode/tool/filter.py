from dataclasses import dataclass, field

ALL_AGENT_DISALLOWED_TOOLS = frozenset({"Agent"})
TEAMMATE_ALLOWED_TOOLS = (
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskUpdate",
    "SendMessage",
)
CUSTOM_AGENT_DISALLOWED_TOOLS: frozenset[str] = frozenset()
ASYNC_AGENT_ALLOWED_TOOLS = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "bash",
        "load_skill",
        "install_skill",
        "LoadSkill",
        "InstallSkill",
    }
)


@dataclass
class FilterParams:
    all: list[str]
    source: int
    background: bool
    allowed: list[str] = field(default_factory=list)
    disallowed: list[str] = field(default_factory=list)
    fork: bool = False
    teammate: bool = False
    legacy_task_tools: bool = False


def apply_agent_tool_filter(params: FilterParams) -> list[str]:
    blocked = set(params.disallowed)
    blocked.update(("TeamCreate", "TeamDelete"))
    if not params.fork and not params.teammate:
        blocked.update(ALL_AGENT_DISALLOWED_TOOLS)
    if not params.teammate and not params.legacy_task_tools:
        blocked.update(TEAMMATE_ALLOWED_TOOLS)
    if params.source != 0:
        blocked.update(CUSTOM_AGENT_DISALLOWED_TOOLS)
    allowed = set(params.allowed)
    if params.teammate:
        allowed = (allowed or set(params.all)) | set(TEAMMATE_ALLOWED_TOOLS)
        return [
            name
            for name in dict.fromkeys([*params.all, *TEAMMATE_ALLOWED_TOOLS])
            if name in allowed and name not in blocked
        ]
    return [
        name
        for name in params.all
        if name not in blocked
        and (not allowed or name in allowed)
        and (
            not params.background
            or name in ASYNC_AGENT_ALLOWED_TOOLS
            or name.startswith(("mcp__", "skill__"))
            or (params.fork and name == "Agent")
        )
    ]
