import asyncio
from typing import TYPE_CHECKING

from termcode.agent.agent import ApprovalRequest, Event
from termcode.tool import resolve_path, with_cwd
from termcode.worktree import Manager, random_agent_name
from termcode.worktree.create import warn

if TYPE_CHECKING:
    from termcode.agent.agent import Agent, Push
    from termcode.conversation import Conversation


def build_worktree_notice(parent_cwd: str, wt_path: str) -> str:
    return (
        "<worktree-context>\n"
        "你当前在独立的 Git Worktree 中工作。\n"
        f"父目录：{parent_cwd}\n"
        f"你的工作目录：{wt_path}\n"
        "请把父目录下的绝对路径换成本地 Worktree 路径后再读写。\n"
        "编辑文件前，先用 read_file 读取 Worktree 中的文件。\n"
        "</worktree-context>"
    )


async def _execute_with_worktree(
    manager: Manager, agent: "Agent", conv: "Conversation", prompt: str, push: "Push"
) -> str:
    parent_cwd = resolve_path("")
    wt = await manager.create(random_agent_name(), "HEAD", manual=False)

    def forward(event: Event | ApprovalRequest) -> None:
        if isinstance(event, ApprovalRequest):
            push(event)

    failure: Exception | None = None
    try:
        with with_cwd(wt.path):
            result = await agent.run_to_completion(
                conv,
                f"{build_worktree_notice(parent_cwd, wt.path)}\n\n{prompt}",
                push=forward,
            )
    except Exception as exc:
        failure = exc
        result = ""
    finally:
        cleanup = asyncio.create_task(manager.auto_cleanup(wt.name))
        try:
            kept = (await asyncio.shield(cleanup)).kept
        except asyncio.CancelledError:
            await asyncio.gather(cleanup, return_exceptions=True)
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            warn(f"无法自动清理 {wt.name}：{exc}")
            kept = manager.get(wt.name) is not None
    notice = f"\n[Worktree 保留：{wt.path}，分支 {wt.branch}]" if kept else ""
    if failure is not None:
        if kept:
            raise RuntimeError(f"{failure}{notice}") from failure
        raise failure
    return result + notice
