"""自动压缩与手动压缩共用的进度提示。"""

from termcode.agent import CompactEvent, CompactPhase


def format_compact_notice(event: CompactEvent) -> str:
    if event.phase == CompactPhase.BEFORE_AUTO:
        return "正在压缩上下文..."
    if event.phase == CompactPhase.BEFORE_EMERGENCY:
        return "上下文撞墙，自动压缩中..."
    if event.err is not None:
        return f"压缩失败：{event.err}"
    return f"已压缩，token 从 {event.before} 降至 {event.after}"
