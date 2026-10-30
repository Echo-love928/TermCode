# ch06 权限系统验收记录

验收日期：2026-10-30（Asia/Tokyo）。实现与验收采用四份文档及本轮用户确认的规则语义。

## 已确认的实现口径

- 三层配置规则合并，效果优先级为 `deny > ask > allow`，不使用层级覆盖。只有 `default_mode` 按本地 > 项目 > 用户选取。
- 未知/未注册工具、畸形 Bash 参数和不可解析的文件路径始终拒绝；只读命中 ask 规则时拒绝。bypass 和 allow 不能覆盖这些安全默认。
- 永久允许写精确 allow 规则，不能覆盖已有 ask/deny；相同调用若仍有 ask 规则，之后仍需确认。
- 沿用现有 `agent/__init__.py`、`TermCodeApp`、`examples/smoke.py`，新增 `python -m smoke` 入口。模式等基础类型由 `permission/types.py` 定义并通过包门面导出。
- 黑名单固定在模块内，不作为可修改的 Engine 配置字段。项目根解析失败仍返回引擎，但文件操作安全拒绝。

## 执行命令与结果

| 检查 | 实际结果 | 证据 |
| --- | --- | --- |
| Windows/Python 3.14 全量 pytest | 228 passed，2 skipped；跳过符号链接权限与 Windows 不允许的星号文件名 | [原始输出](../evidence/ch06-pytest-windows.txt) |
| Linux/Python 3.12 全量 pytest，`-X dev -W error::RuntimeWarning --timeout=30` | 230 passed；无超时或 RuntimeWarning，符号链接与星号路径用例均通过 | [原始输出](../evidence/ch06-pytest-linux.txt) |
| `ruff check .` | 通过，无告警 | 本轮命令实际返回 `All checks passed!` |
| `ruff format --check .` | 通过，62 个 Python 文件符合格式 | 本轮实际返回 `62 files already formatted` |
| `mypy src/termcode` | 通过，36 个源码文件 | 实际返回 `Success: no issues found in 36 source files` |
| `python -m termcode --version` | `TermCode 0.1.0` | 本轮 CLI 实际输出 |
| `python -m termcode` | 双协议隔离项目均成功进入真实 TUI | [本地 tmux 结果](../evidence/ch06-tmux-results.json) |
| `python -m smoke` | BYPASS 下实际写入 `.cache/smoke-ch06.txt`，返回 `SMOKE_DONE`，退出码 0 | 同上 `python_m_smoke` 字段；另有双协议显式 ask 的非交互拒绝测试 |
| gitignore | 本地权限文件和凭据配置均命中；项目 settings.yaml 可提交 | 实跑 `git check-ignore` |
| provider 适配层 | 无改动 | 实跑 `git diff -- src/termcode/llm`，输出为空 |

Windows 沙箱最初阻止 asyncio 创建本地 socketpair，未进入测试逻辑；允许本地网络后完成上述原生测试。WSL 挂载盘首次导入 SDK 曾超过单用例时限，现将 SDK 预加载移到测试计时之前；行为用例的 30 秒超时仍生效。

## Checklist 实现与集成证据

以下函数均已实际执行；完整结果以最终 pytest 日志为准。

| Checklist 项目 / 验收标准 | 覆盖与实际观察 |
| --- | --- |
| 黑名单硬拦截、不可绕过（AC1） | `test_blacklist_all_modes_and_allow_cannot_override` 覆盖所有模式及 allow；tmux 场景 6 实际显示 `命中危险命令黑名单：rm -rf /`，未执行命令。 |
| 沙箱围栏、防逃逸、祖先回退（AC2） | `test_sandbox_all_file_tools_and_relative_rules`、`test_symlink_existing_and_nonexistent_targets`；Linux 创建真实符号链接并覆盖不存在的深层目标。 |
| 精确/glob、deny 正向、同层优先（AC3/AC5） | `test_command_patterns`、`test_path_patterns`、`test_rule_parsing_and_same_set_priority`；文件 `*` 不跨目录，`**` 跨目录，deny > ask > allow。 |
| 六个友好名路由（AC4） | 五种文件工具的参数化引擎测试，以及 Bash 黑名单/规则测试，均按对应友好名匹配。 |
| 效果优先级（三层合并）（AC5） | `test_cross_layer_effect_priority` 穷举三个来源的六种排列；跨层 deny 压 ask/allow，ask 压 allow，bypass 亦不能覆盖。 |
| 配置降级（AC6） | `test_config_degradation_default_priority_and_ask` 验证文件缺失、非法 YAML、非对象结构、错误列表类型、非法规则条目和根解析失败。 |
| 四模式矩阵与 plan 语义（AC7） | `test_modes_and_complete_matrix` 逐档逐类验证；既有 plan 工具集/提醒/执行防线测试和 tmux 场景 8 通过。 |
| 短路与跳层（AC8） | `test_short_circuit_and_layer_skipping` 将不应进入的层替换成失败探针；Bash 不走路径沙箱，文件不走命令黑名单。 |
| 运行时模式、状态栏、跨轮保持（AC9） | `test_cycle_status_and_mode_persists_across_turns`；tmux 场景 5/6/8 观察 `ACCEPT EDITS`、`BYPASS`、`PLAN` 与 `/do` 返回 default。状态栏不显示 provider 名。 |
| 人在回路三选一（AC10） | `test_approval_choices_loop_feedback_and_reload`；双协议 `test_real_approval_menu_keys` 覆盖 ↑↓、j/k、回车、空格、1/2/3、y/n，默认光标为允许本次。 |
| 永久授权与重载（AC10） | `test_persist_exact_glob_escaping_reload_and_idempotence` 验证转义、精确范围、幂等和新引擎重载；`test_permanent_allow_preserves_stricter_rules_and_mode` 验证 ask/deny 与默认模式不丢失；tmux 场景 4 真正退出并重启 CLI 验证。 |
| 拒绝回灌不中断、保序配对（AC11） | `test_readonly_mixed_batch_concurrent_and_ordered`、`test_cross_protocol_blacklist_sandbox_and_loop_feedback`；拒绝与成功结果保持原调用 ID/顺序，继续请求后续轮。tmux 场景 2 从外部路径改到内部并写入。 |
| 取消安全（AC12） | `test_cancel_while_approving_no_tasks_and_can_resume` 覆盖取消事件、task.cancel、提前关闭生成器；`test_approval_cancel_and_followup_no_task_leaks` 与双协议 tmux 场景 9 验证 Esc/Ctrl+C 不退出程序，下一轮成功。 |
| 只读并发不退化（AC13） | `test_readonly_mixed_batch_concurrent_and_ordered` 的两项允许读取同时进入，峰值并发为 2；被拒项不执行，不产生 ApprovalRequest。既有并发回归同时通过。 |
| 跨协议一致（AC14） | 两协议 SDK/HTTP 集成、双协议九个 tmux 场景通过；真实配置的 OpenAI/Anthropic 兼容端点均通过审批和取消恢复；provider 源文件无修改。 |
| 安全默认三路（AC15） | `test_safe_defaults_cannot_be_overridden`、`test_registry_missing_readonly_flag_is_not_trusted`、`test_readonly_ask_is_denied_and_unknown_is_never_executed`；包括全部模式、宽泛 allow、未知工具、畸形 JSON/参数和只读属性缺失。 |
| ch04/ch05 回归（AC16） | 全量 suite 覆盖多轮、流错误恢复、历史配对、并发、缓存字段传递、规划提醒频率；tmux 覆盖读写多轮、plan/do、取消后恢复。 |
| 规范、密钥保护（AC17） | ruff/mypy/gitignore 检查通过；TUI 和 tmux 捕获逐次检查测试密钥及真实配置密钥不出现在输出；永久授权保存失败日志不包含异常原文。 |
| 启动默认模式（AC18） | 配置优先级测试及 `test_config_plan_startup_and_do_requests_approval`；配置 plan 启动即只向 SDK 暴露只读工具，并附计划提醒。 |
| 可扩展性（AC19） | 已审阅权限包的 types/engine/rule 分工及 provider diff；模式与防御逻辑集中在 permission，agent 只消费 Allow/Deny/Ask，provider 不依赖权限包。 |

## tmux 九场景逐项记录

运行 `python tests/tmux_permission_e2e.py`。以下场景在真实 tmux 中启动 CLI、发送中文对话、通过官方 SDK 与本地 SSE 服务交互并执行真实工具。协议响应为确定性脚本，用于保证边界场景确实触发；真实模型观察另列在下一节。

| 场景 | 两协议实际结果 | 本机证据（OpenAI 示例；Anthropic 使用同名替换） |
| --- | --- | --- |
| 1 default 写需确认 | 看到多行工具/参数/原因/三选菜单，按 1 后写出 `one.txt`，不写权限配置 | [审批](../evidence/tmux-ch06-openai-scene1-approval.txt)、[完成](../evidence/tmux-ch06-openai-scene1.txt) |
| 2 拒绝后改路径 | `../outside.txt` 被拒；后续调用写 `safe.txt` 成功，项目外文件不存在 | [完成](../evidence/tmux-ch06-openai-scene2.txt) |
| 3 菜单交互 | 默认高亮允许本次；Up+Enter 选择拒绝，`reject.txt` 不存在。数字键/其余便捷键由真实 TUI Pilot 验证 | [审批](../evidence/tmux-ch06-openai-scene3-approval.txt) |
| 4 永久授权 | Down+Enter 选择永久，文件含 `Write(persistent.txt)`；退出重启 CLI 后无审批直接写入 `reloaded` | [首次完成](../evidence/tmux-ch06-openai-scene4.txt)、[重启完成](../evidence/tmux-ch06-openai-scene4-reload.txt) |
| 5 acceptEdits | Shift+Tab 后显示 ACCEPT EDITS；写/改直接执行，Bash 仍弹确认 | [写改](../evidence/tmux-ch06-openai-scene5-write.txt)、[命令审批](../evidence/tmux-ch06-openai-scene5-exec-approval.txt) |
| 6 bypass 与黑名单 | 普通命令无审批执行，`rm -rf /` 返回黑名单拒绝 | [实际输出](../evidence/tmux-ch06-openai-scene6.txt) |
| 7 沙箱 | `/etc/passwd` 被拒，返回项目目录之外原因 | [实际输出](../evidence/tmux-ch06-openai-scene7.txt) |
| 8 plan/do | plan 只发送 Read/Glob/Grep 定义，读取后规划；/do 回 default，经批准写出 planned.txt | [计划](../evidence/tmux-ch06-openai-scene8-plan.txt)、[执行](../evidence/tmux-ch06-openai-scene8-do.txt) |
| 9 取消 | Esc 与 Ctrl+C 都干净返回空闲；随后对话成功，cancelled.txt 不存在 | [Esc 恢复](../evidence/tmux-ch06-openai-scene9-Escape-resume.txt)、[Ctrl+C 恢复](../evidence/tmux-ch06-openai-scene9-C-c-resume.txt) |

[汇总 JSON](../evidence/ch06-tmux-results.json) 记录两协议九场景通过、永久授权重载成功、smoke 成功，以及四次退出的退出码 0/终端状态恢复。

危险命令场景使用独立 tmux 服务中的无破坏 `rm` 替身作额外保护。实际观察到的是执行前黑名单拒绝；没有调用替身。最初验收脚本重启时误读旧界面，导致过早输入；已修正为等待新进程 token 计数为零的空闲界面后重跑成功。

## 真实模型观察

运行 `python tests/tmux_permission_e2e.py --live`，使用现有配置中的两个兼容端点，在隔离测试项目发送真实对话：

- OpenAI 兼容端点 `deepseek-flash`：模型实际请求 write_file，出现 default 审批，选择允许后写出 `live.txt`，内容为 `live permission ok`；第二次写请求等待审批时 Esc 取消，之后发送新消息并收到“继续成功”。
- Anthropic 兼容端点 `glm-5.3-flash`：同样观察到真实 write_file 审批、允许后文件内容正确、等待审批时取消以及下一轮恢复成功。
- 两个真实会话退出码均为 0，前后终端模式一致。

证据：[真实模型结果](../evidence/ch06-live-results.json)、[OpenAI 审批](../evidence/tmux-ch06-live-openai-write-approval.txt)、[Anthropic 审批](../evidence/tmux-ch06-live-anthropic-write-approval.txt)、[OpenAI 恢复](../evidence/tmux-ch06-live-openai-resume.txt)、[Anthropic 恢复](../evidence/tmux-ch06-live-anthropic-resume.txt)。

真实模型验证范围为写审批及取消恢复；九场景全部覆盖来自真实 tmux + 确定性协议服务，不将后者描述为真实模型自主推理测试。缓存创建计数等 ch05 既有服务限制不在本章重新认证。

## 非交互补充验收

`test_smoke_explicit_ask_in_bypass_denied_without_blocking` 在 OpenAI/Anthropic 两协议下加载显式 `Write` ask 规则。虽然 smoke 使用 BYPASS，规则仍触发审批；非交互入口明确拒绝，模型收到工具拒绝结果并继续回复，未创建文件且不阻塞。

## 已知边界

沿用 Spec 的范围：黑名单为启发式，Bash 不做路径沙箱；Glob/Grep 只围栏搜索根，深层遍历围栏不属于本章。原始 tmux 证据可能包含本机路径，放在已忽略的 `docs/evidence/` 中；源码和配置示例不含真实密钥。
