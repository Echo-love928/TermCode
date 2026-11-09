# Ch14 Worktree 验收记录

2026-11-09 在 Ubuntu WSL 的 Python 3.12 环境运行 TermCode CLI、Git 和真实 tmux。六个场景各用独立的临时 Git 仓库和 tmux 会话。先用本地 HTTP/SSE 供应商复现模型调用，检查窗格输出、会话工具结果、文件内容和分支状态。

## tmux 场景

| 场景 | 结果 | 观察证据 |
| --- | --- | --- |
| 1. 子 Agent 写入隔离副本 | 通过 | 窗格出现 `Agent(...)`。主目录的 `scratch_ch14.txt` 保持 `MAIN`，副本中为 `SUBAGENT`。Agent 结果列出保留副本和分支，随后手动清理成功。 |
| 2. 只读子 Agent 自动清理 | 通过 | Agent 返回 README 总结。任务结束后临时副本和对应分支均已清理。 |
| 3. 手动创建、列表和删除 | 通过 | `list` 显示 `demo-feature` 及 `[manual]`，副本包含 README，Git 确认分支为 `worktree-demo-feature`，删除后目录和分支均消失。 |
| 4. 退出时保护变更 | 通过 | `exit --remove` 拒绝删除并保留 `test.txt`，加 `--discard` 后删除目录和分支，会话文件写为 `null`。 |
| 5. 进入后工具使用显式工作目录 | 通过 | 相同的 `read_file(path="probe.txt")` 在副本中返回 `in-worktree-only`，退出后在主目录中报告文件不存在。 |
| 6. Slug 校验 | 通过 | `../etc` 和 `..` 均被拒绝，合法的 `normal_one` 创建及清理成功。 |

使用项目配置的智谱模型 `glm-5.3-flash` 执行 `python tests/tmux_worktree_e2e.py --live`，同样通过六个场景。模型完成了隔离写入、README 总结和进入及退出后的两次相对路径读取。两次运行各自保留了结构化结果和逐场景 tmux 抓屏。 [本地 HTTP/SSE 结果](../../.cache/tmux-e2e/termcode-e2e-0266cd30d5/results.json) · [智谱实跑结果](../../.cache/tmux-e2e/termcode-e2e-4caaeaa30d/results.json)

## 自动化验证

- `tests/test_worktree_manager.py` 在真实临时 Git 仓库中检查 slug、初始化设置、Git 非交互执行、快速恢复、会话持久化、重启后的提交保护及过期清理。
- `tests/test_tool_ctx.py` 检查六个工具的工作目录、原有参数 schema、任务之间的 cwd 隔离和文件权限检查。
- `tests/test_agent_worktree.py` 检查子 Agent 启动及回收、启动时会话恢复、非 Git 环境降级、异步清理和 CLI 工具列表。
- Windows 定向 pytest 为 69 项通过。符号链接创建在 WSL 中验证。
- WSL 全量 `python -m pytest -q --tb=short -p no:cacheprovider` 为 655 项通过，用时 169.30 秒。
- `python -m termcode --help`、`ruff check` 和 `git check-ignore` 通过。

## 实现取舍

创建使用 `git worktree add -b`。如果同名分支已经存在，命令会报错并保留该分支的提交历史。管理器把创建时的提交和手动创建标记保存在该 Worktree 的 Git 管理目录中，供重启后的删除保护和过期清理使用。
