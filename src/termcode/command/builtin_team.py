"""团队的人工查询与生命周期命令。"""

import shlex

from termcode.team.tasks import Store

from .ui import UI

USAGE = (
    "/team list | /team info <name> | /team delete <name> [--force]"
    " | /team kill <member>"
)


async def show_team_usage(ui: UI) -> None:
    ui.println(USAGE)


async def handle_team(ui: UI, args: str) -> None:
    if not args.strip():
        await show_team_usage(ui)
        return
    manager = ui.team_manager()
    if manager is None:
        ui.error("Agent Teams 不可用")
        return
    try:
        parts = shlex.split(args)
        match parts:
            case ["list"]:
                teams = manager.list_()
                if not teams:
                    ui.println("暂无团队")
                for team in teams:
                    active = sum(m.is_active is not False for m in team.members)
                    ui.println(
                        f"{team.sanitized_name}  {team.backend}"
                        f"  {len(team.members)} 成员"
                        f"  [{active}/{len(team.members)}] 活跃"
                    )
            case ["info", name]:
                team = manager.require(name)
                count = len(await Store(team.config_dir).list_())
                ui.println(
                    f"{team.sanitized_name} · {team.backend} · {count} 个共享任务"
                )
                ui.println(str(team.config_path))
                for member in team.members:
                    ui.println(
                        f"{member.name}  {member.agent_id}  {member.backend_type}"
                        f"  is_active={member.is_active}"
                        f"  {member.worktree_path}  pane_id={member.pane_id}"
                    )
            case ["delete", name] | ["delete", name, "--force"]:
                await manager.delete(name, "--force" in parts[2:])
                ui.println(f"已删除团队 {name}")
            case ["kill", target]:
                member = await manager.kill(target)
                ui.println(f"已终止队员 {member.name}")
            case _:
                ui.error(USAGE)
    except (OSError, RuntimeError, ValueError) as exc:
        ui.error(str(exc))
