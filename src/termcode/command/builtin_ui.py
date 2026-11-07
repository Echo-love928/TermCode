"""改变界面或会话的动作由 UI 实现，忙碌检查统一在分发入口完成。"""

from termcode.permission import Mode

from .ui import UI


async def handle_exit(ui: UI) -> None:
    ui.quit()


async def handle_plan(ui: UI) -> None:
    ui.set_mode(Mode.PLAN)
    ui.println("已进入计划模式（只读工具）；/do 开始执行。")


async def handle_compact(ui: UI) -> None:
    if not ui.idle():
        ui.error("请等待当前任务完成")
        return
    ui.force_compact()


async def handle_resume(ui: UI) -> None:
    if not ui.idle():
        ui.error("请等待当前任务完成")
        return
    ui.open_resume_menu()


async def handle_clear(ui: UI) -> None:
    ui.clear_and_new_session()
