from dataclasses import dataclass, field

ALL_AGENT_DISALLOWED_TOOLS = frozenset({"Agent"})
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


def apply_agent_tool_filter(params: FilterParams) -> list[str]:
    blocked = set(params.disallowed)
    if not params.fork:
        blocked.update(ALL_AGENT_DISALLOWED_TOOLS)
    if params.source != 0:
        blocked.update(CUSTOM_AGENT_DISALLOWED_TOOLS)
    allowed = set(params.allowed)
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
