# ch05 系统提示工程化验收记录

验收日期：2026-10-29（Asia/Tokyo）。依据同目录 Spec、Plan、Tasks、Checklist。

## 实现结果

T1–T12 的代码开发已完成。原 `prompt.py` 迁为 `prompt/` 包，保留图片横幅与
`EXECUTE_DIRECTIVE` 的原导入路径。Agent 继续位于仓库现有 `agent/__init__.py`，
工具描述通过现有 `description()` 方法提供，不为文档中的示意路径重复创建实现。

- 七个固定模块按 10–70 排序，三个可选空槽按 80–100 预留，空内容自动跳过。
- 稳定提示、环境、临时提醒通过 `Request` 分开传递。环境每次 run 在线程中采集，
  git 外调超时 2 秒，状态正文最多 20 行、3000 字符，超出时另附总数；失败时省略该项。
- Anthropic 在稳定 system 文本块上设置唯一缓存断点，环境块位于其后且无断点。
  OpenAI 使用一条 stable 在前、environment 在后的 system 消息，保留兼容 base_url。
- 两协议分别解析缓存写入/读取字段，缺失或空值归零；Agent 透传到用量事件。
- reminder 每轮临时构造，不写入 Conversation。Anthropic 并入末条 user 内容块，
  保留 tool_result 在前；OpenAI 追加尾部 user 消息。
- 规划模式第 1、5、9…轮完整提醒，其余轮精简；每次新 run 重置节奏。
  工具定义过滤与执行端只读防护均保留，`/do` 恢复全工具并执行计划。
- `examples/smoke.py` 连发两条消息并打印缓存用量；正文合并后脱敏，防止密钥跨流片段泄漏。
  TUI 状态栏继续只展示原有输入/输出累计用量。

## 已执行检查

| 检查 | 实际结果与证据 |
| --- | --- |
| Windows / Python 3.14.3 全量 pytest | 134 passed，62.08s；本机 `.cache/ch05-pytest-windows.txt` |
| Ubuntu WSL / Python 3.12.16 全量 pytest | 133 passed，140.70s；另新增 smoke 测试 1 passed，6.90s；本机 `.cache/ch05-pytest-linux.txt` |
| `ruff check .` / `ruff format --check .` | 通过；48 个 Python 文件格式合规 |
| `mypy src/termcode` | Windows 与 Linux 均通过，28 个源文件；使用分平台缓存目录复核，避免共享缓存冲突 |
| ch05 本地 SSE + 真 tmux + CLI + 官方 SDK | 见 [本地汇总](../evidence/ch05-tmux-results.json) |
| ch05 真实模型 + 真 tmux | OpenAI 兼容 deepseek-flash、Anthropic 兼容 glm-5.3-flash；四个会话退出码均 0，stty 前后一致；见 [真实汇总](../evidence/ch05-live-results.json) |
| ch04 tmux 回归 | 两协议多轮读写、并发、Esc/Ctrl+C 取消、网络错误恢复、计划执行、用量与终端复原通过；见 [回归汇总](../evidence/ch04-tmux-results.json) |

Windows 默认 pytest 临时目录访问受限，使用项目内独立 `--basetemp` 重跑通过。
测试环境中的 git 目录发现通过 `GIT_CEILING_DIRECTORIES` 隔离，避免项目内临时目录被误认为非 git 目录。
更早一次沙箱内挂起的 pytest 进程已按本次进程 PID、命令行与启动时间核实后清理。

## Checklist 逐项证据

| 检查项 | 验证依据 |
| --- | --- |
| 模块顺序、扩展、空槽 | `test_prompt.py::test_module_order_extension_and_empty_slots`，验证模块增挂后按优先级插入 |
| 稳定内容确定性 | `test_stable_prompt_independent_of_environment`；`test_anthropic_system.py` 两次改变环境、比较实际 HTTP 请求稳定块与工具 |
| 环境内容、采集降级、摘要上限 | `test_environment_render_and_no_environment_variable_leak`、`test_git_failure_is_bounded_and_degrades`、`test_missing_cwd_degrades`、`test_git_status_summary_is_bounded` |
| 双重强化与标签语义 | `test_double_reinforcement_and_reminder_semantics`，同时检查系统提示与 edit_file/bash 描述 |
| 缓存字段与断点序列化 | `test_cache_usage_parsing` 覆盖两协议字段存在、缺失、null、零值；`test_system_boundary_reminder_and_history_untouched` 检查 SDK 实际发出的缓存边界 |
| 规划节奏、模式切换、缓存透传 | `test_agent.py::test_request_modes_reminder_cadence_and_cache_passthrough`；本地 tmux 六轮请求观察到完整/精简/精简/精简/完整/精简 |
| 历史合法、不持久化 | `test_system_boundary_reminder_and_history_untouched` 覆盖 user/tool/assistant/空历史；Agent 测试检查历史不含 reminder；真实模型规划与取消恢复无 400 |
| 跨协议一致 | 两协议实际 HTTP 请求断言相同模块和提醒；本地与真实 tmux 均运行规划、执行、环境和取消场景 |
| ch04 不回归 | 原有 Agent 并发、上限、未知工具、取消与流错误测试保留；另跑 `tests/tmux_loop_e2e.py` |
| 界面不阻塞 | `test_environment_collection_does_not_block_loop`；git 有 2 秒超时；tmux 观察等待期间计时、输入与取消正常 |
| 启动与代码规范 | 完整 pytest、ruff、mypy；tmux 以 `python -m termcode` 启动，退出码与 stty 校验 |
| 密钥不回显 | TUI 原有脱敏测试；新增 `test_smoke_cache_output_and_fragmented_secret`；tmux 每次截图与 smoke 输出均核对实际配置密钥未出现 |

## tmux 真实场景观察

对两种真实模型均输入自然语言任务：依次读取五个依据文件，只做创建摘要文件的计划，
等待 `/do` 后再执行。规划阶段目标文件不存在，批准后出现 `write_file`，目标文件有实际摘要内容，
模型还调用 `read_file` 回读校验。终端完整记录可见连续工具调用与最终答复。

| Checklist 场景 | 实际结果 | 证据 |
| --- | --- | --- |
| 1：Anthropic 首轮缓存写入、次轮读取 | **部分验证，严格标准未通过**。首轮 cache_write=0/cache_read=0；次轮 cache_write=0/cache_read=704。已观察到读取计数，未观察到正写入计数。 | [Anthropic smoke](../evidence/ch05-live-anthropic-smoke.txt) |
| 2：规划多轮后 `/do` | 两模型先只读调查、输出计划，批准后实际创建摘要；本地请求另逐轮校验注入节奏。 | [OpenAI 完整记录](../evidence/tmux-ch05-True-openai-exit.txt)、[Anthropic 完整记录](../evidence/tmux-ch05-True-anthropic-exit.txt) |
| 3：提醒不被当作用户提问 | 模型产出任务计划与结果，没有复述或回应标签正文。 | 上述两份完整记录；脚本检查无 `<system-reminder>` 回显 |
| 4：环境感知 | 不调用工具即回答实际工作目录、linux、2026-10-29。 | [OpenAI 环境问答](../evidence/tmux-ch05-True-openai-environment.txt)、[Anthropic 环境问答](../evidence/tmux-ch05-True-anthropic-environment.txt) |
| 5：规划中 Esc 取消后继续 | 取消回空闲，随后 `E2E_RESUME_OK` 正常回复，无 400；原有工具运行中取消由 ch04 tmux 回归另覆盖。 | 两份完整记录的取消和后续答复 |
| 6：非 git 目录 | 在 `/tmp/termcode-ch05-*` 独立目录启动真实 CLI，两模型均正常对话、正常退出。 | [OpenAI 非 git](../evidence/tmux-ch05-True-openai-nongit.txt)、[Anthropic 非 git](../evidence/tmux-ch05-True-anthropic-nongit.txt) |

OpenAI 兼容端点两次 smoke 的 cache_read 均为 1408，cache_write 按本章接口约定为 0；
见 [OpenAI smoke](../evidence/ch05-live-openai-smoke.txt)。

**尚未完成的验收：** Checklist 场景 1 / AC4 的「首轮 cache_write > 0」。
当前仅配置 Anthropic 兼容端点，实际解析值为 0（经后续复查为字段未返回）；无法据此声称已满足正缓存创建计数，
也不能把兼容端点的读取计数当作官方 Anthropic 原生缓存验收。
需在具备原生缓存创建计数的 Anthropic 服务配置下重跑 smoke。
请求中的断点及写入/读取字段解析已由真实 SDK + 本地 HTTP 测试验证。

原始证据仅本机保存于已忽略的 `docs/evidence/`；不包含真实配置或请求头。
缓存实现依据 [Anthropic 官方文档](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
与 [OpenAI 官方示例](https://developers.openai.com/cookbook/examples/prompt_caching101)。

## 缓存诊断修复复验

日期：2026-10-29（Asia/Tokyo）。用户已明确选择「继续使用当前智谱端点，保留严格验收待测」。

**根因：** 原始响应的 message_start 仅提供 input/output，message_delta 提供
cache_read_input_tokens，但两个事件都没有 cache_creation_input_tokens。
SDK 最终用量中该字段为 None，客户端按 N6 约定转成 0。之前的 smoke 没有保留
字段是否可用的区别，导致未报告的计数看起来像服务端实际报告了零写入。
此外，普通 smoke 重用固定前缀，不能保证首轮一定是冷缓存。

**排除验证：** 使用独立标识和长人工提示，首轮输入 3060、缓存读取 0；
次轮缓存读取 3008。前后两轮原始事件均没有缓存创建字段，SDK 也未丢弃该字段。
证据见 [原始用量诊断](../evidence/ch05-cache-probe.json)。这证明当前端点未报告创建量，
不能据此推断没有建立缓存，也不能据此确认其支持原生显式缓存计费语义。

**修复：**

- `llm.Usage` 和 `agent.Usage` 新增 `cache_write_reported`，由 Anthropic 适配器依据
  响应是否提供非空创建计数赋值，并透传到 smoke；数值缺省为零的原约定保持不变。
- `examples/smoke.py --check-cache` 每次生成新的独立标识与长人工前缀，第二轮逐字复用，
  不发送项目系统提示、环境或工具定义，并禁用思考。
- 诊断仅在首轮确有正写入、次轮确有正读取时返回 0 / write_read_verified。
  命中读取但未报告创建计数时返回 2 / read_verified_write_unreported；
  已报告零创建但命中读取时单独标为 read_verified_write_not_observed；未观察到读取返回 1。
- 不用输入量、缓存未命中量或读取量推算写入量，不放宽 AC4 的原生标准。

**回归证据：** 新增断言在修复前因缺少来源标记失败；修复后全量 pytest
141 passed，40.12s。覆盖缺失/null/零/正计数、流末尾 message_delta 字段、Agent 透传、
冷缓存前缀跨轮稳定且跨次不同、原生写读与兼容端点读取的分类。
ruff check、ruff format 和 mypy（28 个源文件）均通过。

tmux 复验汇总见 [缓存诊断修复汇总](../evidence/ch05-cache-fix-tmux-results.json)。
其中原生字段场景使用本地 SSE，只验证请求和解析；智谱场景使用真实服务，只发送人工测试文本。
`python -m termcode` 的读文件、写摘要和退出恢复另在本地 SSE 的真实 tmux 中执行。

| 本次 tmux 复验 | 实际观测 |
| --- | --- |
| TermCode 实际对话 | read_file → write_file → 最终答复，摘要内容与预期相等，退出时 stty 恢复 |
| 原生字段本地 SSE | 首轮写入 8192、次轮读取 8192、reported=true，诊断退出 0 |
| 缺失写入字段本地 SSE | 次轮读取 8192、reported=false，诊断退出 2，明确表示仅验证读取 |
| 智谱真实端点 | 首轮输入 7679、读取 0；次轮读取 7616；两轮 reported=false，状态 read_verified_write_unreported，退出 2 |

两种可控协议场景与真实端点的分类均符合预期；退出码 2 不计作原生 AC4 通过。
真实端点本轮记录见 [冷缓存诊断输出](../evidence/ch05-cache-fix-compatible-live.txt)。

**状态：DONE_WITH_CONCERNS。** 客户端计数来源和冷缓存诊断问题已修复；
按用户决定，原生 Anthropic AC4 继续待测，Checklist 场景 1 保持未勾选。
