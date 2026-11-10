import os

from termcode.config import Config

COORDINATOR_ALLOWED_TOOLS = (
    "Agent",
    "TeamCreate",
    "TeamDelete",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskUpdate",
    "SendMessage",
    "read_file",
    "glob",
    "grep",
    "bash",
)

SYSTEM_PROMPT_SUFFIX = """
<coordinator>
你是团队协调者。按 Research（调研）、Synthesis（汇总）、
Implementation（实施）、Verification（验证）推进。
调研先划分范围，创建团队和共享任务，再把独立问题派给队员。
派出 Agent 或用 SendMessage 交代任务后立即结束本轮，简短告知用户正在等汇报。
在收到队员汇报前，禁止自己调用 read_file、glob、grep、bash 继续探索，
也不要用 sleep 或 TaskList 轮询。队员汇报和空闲通知会自动唤醒你。
只在调研首次定位、汇总阶段阅读队员报告、验证阶段检查 diff 和 Git 状态时使用读工具。
实施时让队员在各自的 Worktree 提交修改。验证时通过 bash 逐个 git merge 队员分支，
检查和解决冲突后验证结果。无法解决时执行 git merge --abort，保留 Worktree，
向用户报告冲突文件与队员的 Worktree 路径。
</coordinator>
""".strip()


def env_truthy(value: str) -> bool:
    return value.lower() in {"1", "true", "yes"}


def is_enabled(cfg: Config) -> bool:
    return cfg.features.coordinator_mode and env_truthy(
        os.environ.get(
            "TERMCODE_COORDINATOR_MODE", os.environ.get("MEWCODE_COORDINATOR_MODE", "")
        )
    )


def allowed_tools() -> list[str]:
    return list(COORDINATOR_ALLOWED_TOOLS)


def system_prompt_suffix() -> str:
    return SYSTEM_PROMPT_SUFFIX
