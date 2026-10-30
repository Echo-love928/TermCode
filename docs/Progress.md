# 开发进度

以用户指定的 `docs/Spec.md`、`docs/Plan.md`、`docs/Tasks.md`、`docs/Checklist.md` 为实现依据。

| 任务 | 状态 | 验证证据 |
| --- | --- | --- |
| T1 项目骨架和依赖 | 完成 | Windows / Linux 独立环境安装成功；uv.lock 校验、sdist / wheel 构建成功 |
| T2 配置模块 | 完成 | 配置结构、字段、YAML、错误脱敏与 CLI 子进程测试通过 |
| T3 配置模板与忽略 | 完成 | 示例可解析；真实配置已列入忽略规则，构建产物不包含真实配置 |
| T4 提示词和小狗横幅 | 完成 | tmux 首屏记录含小狗、名称版本、工作目录和就绪提示 |
| T5 Provider 接口 | 完成 | 两种 SDK 均由同一界面调用；mypy 通过 |
| T6 会话历史 | 完成 | 历史顺序、隔离、只读副本、失败回滚和 HTTP 请求历史测试通过 |
| T7 Anthropic 适配器 | 完成 | 官方 SDK + 本地 HTTP/SSE 测试通过；tmux 可见正文、思考不展示 |
| T8 OpenAI 适配器 | 完成 | 官方 SDK + 本地 HTTP/SSE 测试通过；空 choices、错误与断流均覆盖 |
| T9 TUI 骨架 | 完成 | Pilot 与 tmux 首屏验证通过 |
| T10 流式接入与计时 | 完成 | tmux 观察到等待计时、原始文本增量、完成后的 Markdown |
| T11 供应商选择 | 完成 | tmux 实际方向键选中第二项，状态栏与请求协议一致 |
| T12 界面渲染 | 完成 | 小狗横幅、状态栏、错误样式、历史回看与 38 列窄屏检查通过 |
| T13 入口装配 | 完成 | python -m termcode 在 tmux 中完成对话，5 个会话均正常退出 |
| T14 真实端到端验收 | 本地通过，云端待验证 | tmux 的 5 个本地场景通过；用户已明确选择待配置后再进行真实模型验收 |

## 实现说明

- SDK 默认自动重试与文档不符，两个适配器都显式设为 `max_retries=0`。
- 错误请求回滚未配对的用户消息，避免失败后影响下一次请求。
- `RichLog` 内部回滚并不是终端原生历史；退出时向终端输出已完成的渲染内容，保留完整记录。
- 终端宽度变化后重新排版完成内容；流式区可独立滚动，输入框与状态栏保持可见。
- tmux 实测发现旧式 Alt+Enter 的转义兼容问题，使用限定于 inline 驱动的解析器适配修复；对应真实字节序列有回归测试。
- 快速输入和 Enter 都在输入控件的同一队列处理，避免优先快捷键抢在文本之前执行；新增了连续按键回归测试。
- Python 3.12 的类型注解采用延迟求值；已在 Windows/Python 3.14 与 Linux/Python 3.12 验证。
- 本目录尚未初始化 Git，当前无法按任务组创建提交。


## ch04 Agent Loop

本节以 `docs/ch04-Agent Loop/` 下的 Spec、Plan、Tasks、Checklist 为准；
以上早期章节的单轮行为及失败回滚说明已由本章的循环与历史收尾规则替代。

| 任务 | 状态 | 验证证据 |
| --- | --- | --- |
| T1 用量类型与流事件 | 完成 | 两协议 SDK 测试验证用量及无 usage 的兼容响应 |
| T2 只读工具分类 | 完成 | 六工具分类、注册顺序、未知工具判定测试通过 |
| T3 历史尾部角色 | 完成 | 空历史、user/assistant 尾部与取消配对测试通过 |
| T4 系统提示 | 完成 | 持续工作提示、Plan 系统后缀及 /do 指令已接入 |
| T5 双协议扩展 | 完成 | SDK/本地 HTTP 测试验证 system_suffix、usage 和三轮工具反馈 |
| T6 ReAct 循环 | 完成 | 上限、未知工具计数重置、并发、取消、错误收尾等 18 项 Agent 测试通过 |
| T7 TUI 接入 | 完成 | Pilot + tmux 验证轮次、用量、并发工具展示、Esc/Ctrl+C、/plan、/do |
| T8 全量及端到端 | 完成 | Windows/Linux 全量各 106 passed；本地双协议 tmux 通过；授权后 deepseek/zhipu 真实多轮、Plan/Do、能力答复及取消恢复通过，四个会话退出码 0 |

完整逐项证据及验证范围见 [ch04 验收记录](ch04-Agent%20Loop/Acceptance.md)。

## ch05 系统提示工程化

T1–T12 开发完成：模块化稳定提示、环境分段、双协议缓存请求与用量解析、
临时 system-reminder、规划提醒频率、工具描述强化及 smoke 已接入。
保留 ch04 多轮、并发、取消、错误恢复及 TUI 现有用量展示。

Windows 全量 134 项通过；Linux 全量 133 项通过，另新增 smoke 测试 1 项通过。
双协议本地 tmux 与真实模型的规划/执行、环境问答、取消恢复、非 git 目录已执行。
真实缓存读取：OpenAI 兼容端点 1408，Anthropic 兼容端点次轮 704。

缓存诊断修复：确认智谱原始响应没有 `cache_creation_input_tokens`，新增
`cache_write_reported` 标记区分未报告与真实零值；`smoke --check-cache` 使用独立人工前缀，
按写读均验证、仅验证读取、未命中分别退出 0/2/1。修复后 Windows 全量 141 项通过。

**原生严格验收保留待测：** 用户已明确选择继续使用当前智谱端点。
该端点未提供缓存创建计数，不能满足 AC4 的正缓存创建计数观测要求；
保留 Checklist 场景 1 未勾选，不推算或伪造写入量。
逐项证据与具体结果见 [ch05 验收记录](ch05-System%20Prompt/Acceptance.md)。


## ch06 权限系统

T1–T13 已完成并验收。用户确认三层规则合并、deny > ask > allow；
未知工具/畸形参数始终拒绝，只读 ask 规则按拒绝处理。四份文档中的冲突已同步。

新增 permission 包，接入工具执行前四层判定和 agent 人在回路；
TUI 支持 Shift+Tab 四模式、审批三选一、Esc/Ctrl+C 取消及常驻模式显示。
永久规则精确写本地配置，保留原有规则和默认模式；provider 层没有改动。

- Linux/Python 3.12：230 passed（开发模式、RuntimeWarning 升级为错误、30 秒超时）。
- Windows/Python 3.14：228 passed、2 skipped；跳过分支均在 Linux 实测通过。
- ruff check、ruff format --check、mypy 全部通过。
- 两协议九个真实 tmux 场景通过，含永久授权退出重启后生效、plan/do、取消恢复。
- 两个已配置真实模型均通过写审批、取消及恢复；`python -m smoke` 写文件不阻塞。

逐项证据与验证范围见 [ch06 验收记录](ch06-Permission%20System/Acceptance.md)。

## ch07 MCP 客户端

T1–T8 已实现并执行验证：新增 MCP 子包、两层 YAML 配置、环境变量展开、
stdio / Streamable HTTP、官方 SDK 新旧协议协商、工具适配、CLI 注册与有界退出。
按现有接口接入权限；只有已成功注册的 MCP 工具能够进入规则与模式判定。

- Windows：283 passed、2 skipped；Linux：285 passed，RuntimeWarning 按错误处理。
- ruff check、ruff format --check、全源码 mypy 与 sdist / wheel 构建通过。
- 两协议 tmux 场景 1–8 及额外的审批取消恢复通过；8 次 CLI 退出码均为 0，终端恢复，子进程无残留。
- 两个已配置真实模型均完成 MCP echo 审批、实际调用及续答。
- **原方案偏差：permission 零修改未满足。** 现有引擎会拒绝扩展工具，已对 engine / rule / persist 做三处必要扩展，并保留未注册工具拒绝。
- 可选 HTTP tmux 场景未运行；HTTP 已通过真实端点的集成测试，不冒充为 tmux 验收。

逐项证据、实现差异与复现命令见 [ch07 验收记录](ch07-MCP%20Server/Acceptance.md)。
