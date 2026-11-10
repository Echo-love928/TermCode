# Agent Teams 验收记录

2026-11-10 在 Ubuntu WSL 的 Python 3.12 环境中启动真实 TermCode CLI 和 tmux。验收脚本向 Lead 窗格输入中文请求，本地 OpenAI 兼容 SSE 服务提供可重复的工具调用。脚本检查实际 Git Worktree、工具结果、邮箱状态、会话存档和窗格输出。每个场景在 WSL 原生临时目录中使用独立的 Git 仓库和 HOME，结果和终端抓屏保存在项目缓存目录。

## 实现与集成

| Checklist 项目 | 验证证据 |
| --- | --- |
| Manager 初始化、落盘、名称清洗及同名后缀 | `test_team_create_concurrent_reload_and_cleanup` 检查配置文件、清洗后的名称及同名并发创建结果。 |
| 三种后端和检测顺序 | `test_registry_detection_and_tool_filter` 检查枚举、tmux 优先级、iTerm2 分支及无二进制时的 in-process 分支。 |
| 邮箱写读、过期锁回收、标记已读及锁冲突 | `test_mailbox_stale_concurrent_and_busy` 检查消息往返、并发追加、过期锁和抢锁耗尽后的消息完整性。 |
| 名称注册与覆盖 | `test_registry_detection_and_tool_filter` 检查名称和 ID 双向查找，以及重名覆盖。 |
| 任务 ID、依赖双向维护、就绪状态 | `test_shared_tasks_maintain_reciprocal_dependencies` 检查 ID 格式、添加和移除依赖、完成前置任务后的就绪状态，以及无效更新回滚。 |
| Coordinator 双开关和工具白名单 | 四组开关组合的单测，以及下表的 Coordinator 场景。 |
| Lead 与队员的工具注册、普通 Agent 兼容 | `test_tools_require_context_and_plan_approval_is_correlated` 检查生命周期和协作工具的作用域。原有 SubAgent、Worktree 和后台任务由回归套件覆盖。 |
| 窗格队员的身份、Worktree、初始邮箱和 CLI 入口 | tmux 场景核对真实进程参数中的 `--agent-id`、首条 Lead 邮件、队员日志和隔离目录。 |
| 成员状态落盘、空闲通知和续派 | 单测用两个 Manager 复现旧内存状态。tmux 场景观察独立队员进程把状态写回为 `false`。in-process 场景和 Fork 单测检查同一队员的会话续写。 |
| 计划审批和消息注入 | 单测拒绝错误或重复的 request ID。计划场景检查队员读到审批、切换到 `default` 并执行写入。 |
| Lead 邮箱轮询和自动续轮 | tmux 场景检查 `[idle]` 消息被标记已读，终端出现 `[team-update]`，并且会话产生后续回复。 |
| `/team list`、`/team info` 和强制清理 | tmux 场景查询团队与队员，随后确认团队目录、队员会话、Worktree 和队员窗格被清理。 |
| 临时文件白名单 | POSIX 单测验证 `write_file` 和 `edit_file` 能写 `/tmp`，`/etc/passwd` 与 Worktree 外的相对路径受限。macOS 的 `/private/tmp` 实机验证待补。 |

## 真实 tmux 场景

| 场景 | 结果 |
| --- | --- |
| tmux 生命周期 | 通过。Lead 创建 `demo`，队员 `alice` 在独立窗格和 Worktree 写入 `hello`，接收续派后追加 `world`。确认队员空闲状态、Lead 自动回复和强制清理。 |
| in-process 生命周期 | 通过。测试将 tmux 和 iTerm2 从被测进程的可见环境中移除，仍用外部 tmux 驱动真实 TUI。队员 `bob` 没有新增窗格，在同一会话先后写入 `step1`、`step2`。 |
| Coordinator Mode | 通过。状态栏显示 `[COORDINATOR]`，尝试调用 `write_file` 返回错误，`bash git status` 返回 Git 状态。Lead 派出队员后结束本轮，收到队员读取 README 的汇报后继续。 |
| Plan 审批 | 通过。planner 的计划进入 Lead 邮箱。批准前目标文件尚未创建，Lead 按 request ID 回复批准后，队员切换到 `default`，写出 `hello world`。 |

[可复跑脚本](../../tests/tmux_team_e2e.py) · [结构化结果](../../.cache/tmux-e2e/termcode-e2e-c69cd063a8/results.json) · [Lead 抓屏](../../.cache/tmux-e2e/termcode-e2e-c69cd063a8/tmux/lead.txt) · [队员窗格抓屏](../../.cache/tmux-e2e/termcode-e2e-c69cd063a8/tmux/member.txt)

## 智谱实模验收

2026-11-10 经用户授权，在隔离仓库和 HOME 中以 `--live` 使用智谱 `glm-5.3-flash`。tmux 生命周期场景通过。Lead 调用 `TeamCreate` 和 `Agent` 创建 `demo` 并派出 `alice`。队员在独立 Worktree 写入 `hello`，Lead 收到汇报后自动回复。同一队员收到 `SendMessage` 续派后追加 `world`。最终核对了文件的两行内容，并确认 `/team delete demo --force` 清除了团队目录、队员窗格、Worktree 和会话。其余三个场景的结果来自上节本地 SSE 验收。

[实模结构化结果](../../.cache/tmux-e2e/termcode-e2e-966a0ee9fd/results.json) · [实模 Lead 抓屏](../../.cache/tmux-e2e/termcode-e2e-966a0ee9fd/tmux/lead.txt) · [实模队员抓屏](../../.cache/tmux-e2e/termcode-e2e-966a0ee9fd/tmux/member.txt)

## 失败回归与工具检查

- 损坏的团队配置触发警告并跳过。模拟写盘失败后，团队创建回滚。
- 邮箱锁重试耗尽会返回错误，已有邮件保留。模拟 tmux 分屏失败后，队员记录、邮箱、Worktree 和会话均已回滚。
- 缺少团队上下文的协作调用返回错误。活跃队员阻止普通删除，空闲后可正常删除。
- `python -m termcode --help`、`ruff check src/`、`ruff format --check src/` 和 `mypy src/termcode/team/` 已通过。WSL 使用已有虚拟环境运行，`uv sync` 步骤因 WSL 未安装 `uv` 未执行。
- WSL 的全量 `pytest` 共 670 项通过。 [回归输出](../../.cache/ch15/pytest-verified.log)

## 实现口径与待验项

- Lead 创建团队后启用共享任务工具，原有后台续派可继续使用 `SendMessage(name, message)`。团队消息使用 `SendMessage(to, content)`。计划提交使用 `plan_approval_request`，系统生成供 Lead 回传的 request ID。
- 保留仓库现有的 `cli.py` 入口，`--team-member` 分支交给 `team/member.py` 运行自治循环。
- 团队配置保存在用户目录。Lead 仅操作当前项目的团队，`/team list` 仍可列出配置。队员同意优雅退出后停止执行，Worktree 和会话保留到团队清理。
- Coordinator 的配置开关是 `features.coordinator_mode`。环境变量优先读取 `TERMCODE_COORDINATOR_MODE`，同时兼容章内示例的 `MEWCODE_COORDINATOR_MODE`。
- iTerm2 已做命令契约单测，当前环境缺少 macOS iTerm2 实机。实现采用 iTerm2 自带的 `it2 session` 命令。 [iTerm2 命令源码](https://github.com/gnachman/iTerm2/blob/master/it2cli/Sources/it2core/Commands/SessionCommand.swift)
