"""供权限规则与 Hook 条件复用的匹配器。"""

import re
from dataclasses import dataclass, field
from typing import Protocol


class Matcher(Protocol):
    def match(self, target: str) -> bool: ...
    def __str__(self) -> str: ...


@dataclass(frozen=True)
class ExactMatcher:
    """匹配完整字面值。"""

    value: str
    ignore_case: bool = False

    def match(self, target: str) -> bool:
        if self.ignore_case:
            return target.casefold() == self.value.casefold()
        return target == self.value

    def __str__(self) -> str:
        return f"={self.value}"


def _glob_regex(
    pattern: str, *, is_file: bool, ignore_case: bool = False
) -> re.Pattern[str]:
    parts = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\" and index + 1 < len(pattern):
            index += 1
            parts.append(re.escape(pattern[index]))
        elif char == "*":
            double = pattern[index + 1 : index + 2] == "*"
            if double:
                index += 1
            if is_file and double and pattern[index + 1 : index + 2] == "/":
                parts.append("(?:.*/)?")
                index += 1
            else:
                parts.append(".*" if double or not is_file else "[^/]*")
        else:
            parts.append(re.escape(char))
        index += 1
    flags = re.DOTALL | (re.IGNORECASE if ignore_case else 0)
    return re.compile("".join(parts), flags)


def match_pattern(pattern: str, target: str, *, is_file: bool = False) -> bool:
    return (
        not pattern
        or _glob_regex(pattern, is_file=is_file).fullmatch(target) is not None
    )


@dataclass(frozen=True)
class GlobMatcher:
    """命令整串通配，路径单星号仅匹配同一层。"""

    pattern: str
    is_command: bool
    ignore_case: bool = False
    _compiled: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_compiled",
            _glob_regex(
                self.pattern, is_file=not self.is_command, ignore_case=self.ignore_case
            ),
        )

    def match(self, target: str) -> bool:
        return self._compiled.fullmatch(target) is not None

    def __str__(self) -> str:
        return self.pattern


@dataclass(frozen=True)
class RegexMatcher:
    """搜索目标字符串中的任意匹配片段。"""

    src: str
    ignore_case: bool = False
    compiled: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        try:
            flags = re.IGNORECASE if self.ignore_case else 0
            object.__setattr__(self, "compiled", re.compile(self.src, flags))
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}") from exc

    def match(self, target: str) -> bool:
        return self.compiled.search(target) is not None

    def __str__(self) -> str:
        return f"~{self.src}"


@dataclass(frozen=True)
class NotMatcher:
    """对内部匹配结果取反。"""

    inner: Matcher

    def match(self, target: str) -> bool:
        return not self.inner.match(target)

    def __str__(self) -> str:
        return f"!{self.inner}"


def compile_matcher(
    pattern: str, *, is_command: bool, ignore_case: bool = False
) -> Matcher:
    if not pattern:
        raise ValueError("empty matcher pattern")
    head, rest = pattern[0], pattern[1:]
    if head == "=":
        return ExactMatcher(rest, ignore_case)
    if head == "~":
        return RegexMatcher(rest, ignore_case)
    if head == "!":
        return NotMatcher(
            compile_matcher(rest, is_command=is_command, ignore_case=ignore_case)
        )
    return GlobMatcher(pattern, is_command, ignore_case)
