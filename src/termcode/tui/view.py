"""将不可信文本转换为可安全展示的终端内容。"""

import re
from collections.abc import Iterable

from rich.console import Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.table import Table
from rich.text import Text


def clean_text(text: str, secrets: Iterable[str] = ()) -> str:
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            text = text.replace(secret, "[已隐藏密钥]")
    return text


def user_block(text: str) -> Text:
    return Text("● " + text, style="bold")


def tool_line(name: str, args: str) -> Text:
    return Text.assemble(("● ", "bold cyan"), (f"{name}({args})", "bold"))


def tool_result_summary(result: str, is_error: bool = False) -> Padding:
    # 界面保留八行摘要，完整的工具级限长结果仍回灌给模型。
    lines = result[:1601].splitlines()
    summary = "\n".join(lines[:8])[:1600]
    if len(lines) > 8 or len(result) > 1600:
        summary += "\n[truncated]"
    return Padding(
        Text("⎿ " + summary, style="red" if is_error else "dim"), (0, 0, 0, 2)
    )


def render_markdown(reply: str, elapsed: float) -> Group:
    return Group(
        Text("●", style="green"),
        Markdown(reply or "（模型未返回正文）"),
        Text(f"完成 · {elapsed:.1f}s", style="dim"),
        Text(""),
    )


def error_message(error: Exception) -> str:
    """不显示 SDK 异常原文，其中可能包含反射的密钥和请求体。"""
    status = getattr(error, "status_code", None)
    name = type(error).__name__
    if status in {401, 403}:
        return f"认证失败（HTTP {status}），请检查密钥和访问权限。"
    if status == 429:
        return "请求受限（HTTP 429），请稍后手动重试。"
    if status == 404:
        return "模型或接口不存在（HTTP 404），请检查 model 和 base_url。"
    if status == 400:
        return "请求被拒绝（HTTP 400），请检查模型是否支持当前协议和 thinking 设置。"
    if status is not None:
        return f"服务商返回错误（HTTP {status}），请检查配置或稍后重试。"
    if "Timeout" in name:
        return "请求超时，请检查网络或稍后重试。"
    if "Connection" in name:
        return "网络连接失败，请检查网络和 base_url。"
    if isinstance(error, RuntimeError):
        return str(error)
    return "请求失败，请检查网络、服务商配置或响应格式。"


def error_block(message: str, elapsed: float) -> Text:
    return Text(f"● 错误：{message}\n耗时 · {elapsed:.1f}s\n", style="bold red")


def status_bar(name: str, model: str) -> Table:
    table = Table.grid(expand=True, padding=(0, 1))
    table.add_column(ratio=1, overflow="ellipsis", no_wrap=True)
    table.add_column(ratio=1, justify="right", overflow="ellipsis", no_wrap=True)
    table.add_row(Text(name, style="green"), Text(model, style="dim"))
    return table
