"""本地查询不会向对话历史注入消息。"""

from .command import Handler
from .registry import Registry
from .ui import UI


def make_help_handler(reg: Registry) -> Handler:
    async def help_handler(ui: UI) -> None:
        commands = reg.visible()
        width = max((len(cmd.name) for cmd in commands), default=0)
        ui.println(
            "\n".join(f"/{cmd.name:<{width}}  {cmd.description}" for cmd in commands)
        )

    return help_handler


async def handle_status(ui: UI) -> None:
    fields = [
        ("Mode:", str(ui.mode())),
        ("Tokens:", f"{ui.usage_in()} in / {ui.usage_out()} out"),
        ("Tools:", f"{ui.tool_count()} enabled"),
        ("Memories:", f"{len(ui.memory_files())} files"),
        ("Model:", ui.model_name()),
        ("Directory:", ui.cwd()),
    ]
    width = max(len(key) for key, _ in fields)
    ui.println(
        "TermCode Status\n\n"
        + "\n".join(f"{key:<{width}} {value}" for key, value in fields)
    )


async def handle_memory(ui: UI) -> None:
    ui.println("\n".join(ui.memory_files()) or "无已加载的记忆文件")


async def handle_permission(ui: UI) -> None:
    ui.println(str(ui.mode()))


async def handle_session(ui: UI) -> None:
    ui.println(f"Session: {ui.session_id()}\nPath: {ui.session_path()}")
