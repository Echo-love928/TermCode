"""键盘补全状态机；最多展示八行，光标移动时滚动窗口。"""

from dataclasses import dataclass, field

from rich.text import Text

from termcode.command import Command, Registry

MAX_ROWS = 8


@dataclass(slots=True)
class CompletionMenu:
    items: list[Command] = field(default_factory=list)
    cursor: int = 0
    offset: int = 0
    active: bool = False
    rows: int = MAX_ROWS

    def update(self, input_text: str, reg: Registry) -> None:
        if not input_text.startswith("/") or any(c in input_text for c in "\n\r"):
            self.hide()
            return
        previous = self.selected()
        self.items = reg.prefix_match(input_text)
        self.active = True
        if previous in self.items:
            self.cursor = self.items.index(previous)
        else:
            self.cursor = 0
        self._clamp()

    def set_rows(self, rows: int) -> None:
        self.rows = min(MAX_ROWS, max(1, rows))
        self._clamp()

    def _clamp(self) -> None:
        self.cursor = min(max(self.cursor, 0), max(len(self.items) - 1, 0))
        self.offset = min(self.offset, self.cursor)
        self.offset = max(self.offset, self.cursor - self.rows + 1)
        self.offset = min(self.offset, max(len(self.items) - self.rows, 0))

    def move_up(self) -> None:
        self.cursor -= 1
        self._clamp()

    def move_down(self) -> None:
        self.cursor += 1
        self._clamp()

    def selected(self) -> Command | None:
        return self.items[self.cursor] if self.items else None

    def hide(self) -> None:
        self.active = False
        self.items = []
        self.cursor = self.offset = 0

    def render(self, width: int) -> Text:
        if not self.active:
            return Text("")
        if not self.items:
            return Text("无匹配", style="dim", no_wrap=True, overflow="ellipsis")
        width = max(1, width)
        column = max(len(cmd.name) for cmd in self.items)
        result = Text(no_wrap=True, overflow="ellipsis")
        visible = self.items[self.offset : self.offset + self.rows]
        for index, cmd in enumerate(visible, start=self.offset):
            if index != self.offset:
                result.append("\n")
            # 滚动指示与候选共用一行，不额外挤占状态栏区域。
            indicator = "›" if index == self.cursor else " "
            if index == self.offset and self.offset:
                indicator = "↑"
            if index == self.offset + self.rows - 1 and index < len(self.items) - 1:
                indicator = "↓"
            row = Text(f"{indicator} /{cmd.name:<{column}}  {cmd.description}")
            row.truncate(width, overflow="ellipsis")
            if index == self.cursor:
                row.stylize("reverse")
            result.append_text(row)
        return result
