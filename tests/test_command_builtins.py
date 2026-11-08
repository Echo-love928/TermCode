"""验证内置命令种类、实际输出、注入文本与忙碌限制。"""

from termcode.command import Kind, NopUI, Registry, register_builtins
from termcode.permission import Mode
from termcode.prompt import EXECUTE_DIRECTIVE

NAMES = [
    "clear",
    "compact",
    "do",
    "exit",
    "help",
    "hooks",
    "memory",
    "permission",
    "plan",
    "resume",
    "review",
    "session",
    "status",
]


class RecordingUI(NopUI):
    def __init__(self):
        self.output = []
        self.errors = []
        self.calls = []
        self.current_mode = Mode.PLAN
        self.busy = False

    def println(self, msg):
        self.output.append(msg)

    def error(self, msg):
        self.errors.append(msg)

    def mode(self):
        return self.current_mode

    def set_mode(self, mode):
        self.current_mode = mode
        self.calls.append(("mode", mode))

    def inject_and_send(self, label, text):
        self.calls.append(("send", label, text))

    def idle(self):
        return not self.busy

    def force_compact(self):
        self.calls.append(("compact",))

    def memory_files(self):
        return ["MEMORY.md", "user_preference_style.md"]


def registry():
    reg = Registry()
    register_builtins(reg)
    return reg


def test_register_builtins_all_registered():
    commands = registry().visible()
    assert [cmd.name for cmd in commands] == NAMES
    assert sum(cmd.kind == Kind.LOCAL for cmd in commands) == 6
    assert sum(cmd.kind == Kind.UI for cmd in commands) == 5
    assert sum(cmd.kind == Kind.PROMPT for cmd in commands) == 2
    assert all(cmd.description and not cmd.hidden for cmd in commands)


def test_register_builtins_no_collision():
    registry()


async def test_register_builtins_handlers_run_on_nop_ui():
    for cmd in registry().visible():
        await cmd.handler(NopUI())


async def test_help_uses_current_registry_and_hides_hidden():
    from termcode.command import Command

    reg = registry()
    reg.register(Command("extra", "扩展", Kind.LOCAL, reg.lookup("status").handler))
    reg.register(
        Command("secret", "隐藏", Kind.LOCAL, reg.lookup("status").handler, hidden=True)
    )
    ui = RecordingUI()
    await reg.lookup("help").handler(ui)
    rows = ui.output[0].splitlines()
    assert len(rows) == len(NAMES) + 1
    assert [row.split()[0][1:] for row in rows] == sorted(NAMES + ["extra"])
    assert {
        len(row) - len(reg.lookup(row.split()[0][1:]).description) for row in rows
    } == {13}
    assert "secret" not in ui.output[0]


async def test_handle_status_prints_all_keys():
    ui = RecordingUI()
    await registry().lookup("status").handler(ui)
    assert len(ui.output) == 1
    rows = ui.output[0].splitlines()[2:]
    assert [row.split()[0] for row in rows] == [
        "Mode:",
        "Tokens:",
        "Tools:",
        "Memories:",
        "Model:",
        "Directory:",
    ]
    assert "plan" in rows[0] and "2 files" in rows[3]
    assert {len(row[:11]) for row in rows} == {11}
    assert all(row[len(row.split()[0]) : 11].isspace() for row in rows)


async def test_handle_compact_blocks_when_busy():
    ui = RecordingUI()
    ui.busy = True
    await registry().lookup("compact").handler(ui)
    assert ui.errors == ["请等待当前任务完成"]
    assert not ui.calls


async def test_handle_do_sets_mode_and_injects():
    ui = RecordingUI()
    await registry().lookup("do").handler(ui)
    assert ui.calls == [("mode", Mode.DEFAULT), ("send", "/do", EXECUTE_DIRECTIVE)]


async def test_review_only_injects_and_memory_only_lists():
    ui = RecordingUI()
    await registry().lookup("review").handler(ui)
    assert ui.calls[0][:2] == ("send", "/review")
    assert "审查" in ui.calls[0][2]
    await registry().lookup("memory").handler(ui)
    assert ui.output == ["MEMORY.md\nuser_preference_style.md"]
