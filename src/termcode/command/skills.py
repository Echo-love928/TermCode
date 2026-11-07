"""注册 Skill 斜杠命令和本地管理入口。"""

from collections.abc import Callable

import yaml

from termcode.skills.loader import SkillLoader

from .command import ArgumentHandler, Command, Kind
from .registry import Registry
from .ui import UI


def _skill_handler(loader: SkillLoader, name: str) -> ArgumentHandler:
    async def run(ui: UI, args: str) -> None:
        skill = loader.get(name)
        if skill is None:
            ui.error(f"未知 Skill：{name}")
            return
        ui.run_skill(skill, args)

    return run


def _command(
    name: str, description: str, kind: Kind, handler: ArgumentHandler
) -> Command:
    async def without_args(ui: UI) -> None:
        await handler(ui, "")

    return Command(name, description, kind, without_args, argument_handler=handler)


def register_skill_commands(registry: Registry, loader: SkillLoader) -> None:
    registry.set_skill_commands(
        [
            _command(
                name,
                f"{description} [skill]",
                Kind.PROMPT,
                _skill_handler(loader, name),
            )
            for name, description in loader.get_catalog()
        ]
    )


def register_skill_manager(
    registry: Registry, loader: SkillLoader, refresh: Callable[[], None]
) -> None:
    async def manage(ui: UI, args: str) -> None:
        match args.split():
            case [] | ["list"]:
                ui.println(
                    "\n".join(
                        f"  {name:<20} {description}  [{loader.get_source_label(name)}]"
                        for name, description in loader.get_catalog()
                    )
                    or "无已加载的 Skill"
                )
            case ["info", name]:
                skill = loader.get(name)
                if skill is None:
                    ui.error(f"未知 Skill：{name}")
                    return
                metadata = {
                    "name": skill.name,
                    "description": skill.description,
                    "mode": skill.mode,
                    "context": skill.context,
                    "model": skill.model,
                    **skill.frontmatter,
                }
                ui.println(
                    yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
                    + f"source: {loader.get_source_label(name)}\n"
                    f"path: {skill.source_path}\nis_directory: {skill.is_directory}"
                )
            case ["reload"]:
                if not ui.idle():
                    ui.error("请等待当前任务完成")
                    return
                loader.reload()
                refresh()
                ui.println(f"已重新加载 Skill（{len(loader.get_catalog())} 个）")
            case _:
                ui.error("用法：/skill list | /skill info <name> | /skill reload")

    registry.register(_command("skill", "管理已加载的 Skill", Kind.LOCAL, manage))
