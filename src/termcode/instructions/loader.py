"""带边界、环路与深度保护的指令展开器。"""

import re
from pathlib import Path


class Loader:
    def __init__(
        self, project_root: str, user_home: str | None = None, max_depth: int = 5
    ) -> None:
        self.root = Path(project_root).resolve()
        self.user = (Path(user_home) if user_home else Path.home()) / ".termcode"
        self.max_depth = max_depth

    def load(self) -> str:
        sources = [
            (self.root / "TERMCODE.md", self.root),
            (self.root / ".termcode/TERMCODE.md", self.root),
            (self.user / "TERMCODE.md", self.user),
        ]
        return "\n\n".join(
            text
            for path, boundary in sources
            if (text := self._load_file(path, boundary, 1, set()))
        )

    def _load_file(
        self, path: Path, boundary: Path, depth: int, visited: set[Path]
    ) -> str:
        def warning(reason: str) -> str:
            return f"<!-- @include {reason}，已跳过: {path} -->"

        try:
            path = path.resolve()
            if not path.is_relative_to(boundary.resolve()):
                return warning("路径超出允许范围")
            if path in visited:
                return warning("检测到环路")
            if depth > self.max_depth:
                return warning("超过最大嵌套深度")
            data = path.read_bytes()
            if b"\x00" in data[:512]:
                return warning("二进制文件不可读")
            lines = []
            for line in data.decode("utf-8-sig").splitlines():
                match = re.fullmatch(r"@include\s+(.+?)\s*", line)
                if match:
                    target = path.parent / match[1]
                    expanded = self._load_file(
                        target, boundary, depth + 1, visited | {path}
                    )
                    if depth >= self.max_depth:
                        expanded = line + "\n" + expanded
                    lines.append(expanded)
                else:
                    lines.append(line)
            return "\n".join(lines)
        except (OSError, UnicodeError, RuntimeError):
            return ""
