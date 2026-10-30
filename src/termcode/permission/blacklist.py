"""内置危险命令黑名单：启发式、非完备、不可配置放开。"""

import re

_BLACKLIST = (
    re.compile(
        r"\brm\s+"
        r"(?=[^;&|\n]*(?:-[a-zA-Z]*[rR]|--recursive))"
        r"(?=[^;&|\n]*(?:-[a-zA-Z]*f|--force))"
        r"(?:--?[\w-]+\s+)+(?:--\s+)?[\"']?"
        r"(?:/(?:\*)?|~(?:/\*?)?|\$(?:HOME|\{HOME\})(?:/\*?)?)"
        r"[\"']?(?=\s|[;&|]|$)"
    ),
    re.compile(r"\bdd\s+[^\n;&|]*\bof\s*=\s*[\"']?/dev/"),
    re.compile(r":\s*\(\s*\)\s*\{[^}]*\|[^}]*&\s*\}", re.DOTALL),
    re.compile(r"\bmkfs(?:\.[\w-]+)?\b"),
    re.compile(r">\s*[\"']?/dev/(?:sd|hd|nvme|disk)"),
    re.compile(r"\bchmod\s+-R\s+0?777\s+[\"']?/(?:[\"']?(?=\s|$)|\*)"),
)


def hits_blacklist(command: str) -> bool:
    return any(pattern.search(command) for pattern in _BLACKLIST)
