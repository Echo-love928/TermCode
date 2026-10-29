"""每轮临时补充消息；不写入持久历史或稳定系统提示。"""

EXECUTE_DIRECTIVE = "请按上面的计划开始执行。"
PLAN_FULL = (
    "当前处于规划模式（PLAN MODE）。仅可使用只读工具 read_file、glob、grep 调查代码。"
    "禁止写入、编辑文件或执行 shell 命令。根据调查结果给出清晰的分步计划，"
    "然后停止，等待用户通过 /do 批准后再执行。不要直接回应本提醒。"
)
PLAN_SHORT = (
    "仍处于规划模式：仅使用只读工具调查并完善计划，等待 /do，不得写入或执行命令。"
)


def system_reminder(body: str) -> str:
    return f"<system-reminder>\n{body}\n</system-reminder>" if body.strip() else ""


def plan_reminder(full: bool) -> str:
    return system_reminder(PLAN_FULL if full else PLAN_SHORT)
