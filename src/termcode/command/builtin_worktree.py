from .ui import UI

_USAGE = (
    "用法：/worktree create <名称> | list | enter <名称> | "
    "exit [--remove [--discard]] | remove <名称> [--discard]"
)


async def show_worktree_usage(ui: UI) -> None:
    await handle_worktree(ui, "")


async def handle_worktree(ui: UI, args: str) -> None:
    accessor = ui.worktree_accessor()
    if accessor is None:
        ui.error("Worktree 功能未启用")
        return
    parts = args.split()
    if not parts:
        ui.println(_USAGE)
        return
    action, *rest = parts
    if action == "list" and not rest:
        entries = accessor.list()
        if not entries:
            ui.println("当前没有 Worktree")
        for wt in entries:
            flags = (" [active]" if wt.active else "") + (
                " [manual]" if wt.manual else ""
            )
            ui.println(f"{wt.name}  {wt.path}  {wt.branch}{flags}")
    elif action == "create" and len(rest) == 1:
        path, branch = await accessor.create(rest[0])
        ui.println(f"Worktree 已创建：{path}（分支 {branch}）")
    elif action == "enter" and len(rest) == 1:
        path = await accessor.enter(rest[0])
        ui.println(f"已进入 {rest[0]}：{path}")
    elif action == "exit" and (
        not rest
        or rest == ["--remove"]
        or set(rest) == {"--remove", "--discard"}
        and len(rest) == 2
    ):
        removed = await accessor.exit("remove" if rest else "keep", "--discard" in rest)
        ui.println("已退出并删除 Worktree" if removed else "已退出 Worktree")
    elif action == "remove" and rest and rest[1:] in ([], ["--discard"]):
        await accessor.remove(rest[0], "--discard" in rest[1:])
        ui.println(f"已删除 Worktree：{rest[0]}")
    else:
        ui.error(_USAGE)
