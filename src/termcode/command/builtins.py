"""所有内置命令的唯一注册入口。"""

from . import builtin_local as local
from . import builtin_prompt as prompt
from . import builtin_ui as ui
from .builtin_team import handle_team, show_team_usage
from .builtin_worktree import handle_worktree, show_worktree_usage
from .command import Command, Kind
from .registry import Registry


def register_builtins(reg: Registry) -> None:
    for command in [
        Command("clear", "清空对话并开启新会话", Kind.UI, ui.handle_clear),
        Command("compact", "压缩当前上下文", Kind.UI, ui.handle_compact),
        Command("do", "按计划开始执行", Kind.PROMPT, prompt.handle_do),
        Command("exit", "退出 TermCode", Kind.UI, ui.handle_exit),
        Command("help", "查看可用命令", Kind.LOCAL, local.make_help_handler(reg)),
        Command("hooks", "列出已加载的 Hook", Kind.LOCAL, local.handle_hooks),
        Command("memory", "列出已加载的记忆文件", Kind.LOCAL, local.handle_memory),
        Command("permission", "查看权限模式", Kind.LOCAL, local.handle_permission),
        Command("plan", "切换到计划模式", Kind.UI, ui.handle_plan),
        Command("resume", "恢复历史会话", Kind.UI, ui.handle_resume),
        Command("review", "审查当前上下文中的代码", Kind.PROMPT, prompt.handle_review),
        Command("session", "查看会话标识及存档路径", Kind.LOCAL, local.handle_session),
        Command("status", "查看运行状态和用量", Kind.LOCAL, local.handle_status),
        Command(
            "team",
            "查询和管理 Agent Teams",
            Kind.LOCAL,
            show_team_usage,
            argument_handler=handle_team,
        ),
        Command(
            "worktree",
            "创建、切换和管理 Git Worktree",
            Kind.UI,
            show_worktree_usage,
            argument_handler=handle_worktree,
        ),
    ]:
        reg.register(command)
