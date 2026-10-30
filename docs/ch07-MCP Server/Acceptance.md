# ch07 MCP 客户端验收记录

验收日期：2026-10-30。实现包含两层配置、stdio / Streamable HTTP、官方 SDK 双栈协商、工具适配、权限接入和生命周期清理。

## 实现与方案的差异

本章开发前，仓库实际接口与 Plan 中的假设存在差异；原始 Spec / Plan / Tasks 保留，以下明确记录实际实现，不能把不同的约束视为全部通过。

1. **permission 零修改未满足。** ch06 的 `Engine.check()` 明确拒绝非内置工具，规则解析不支持工具名中的连字符和 `*`，永久授权要求参数可解析为本地目标。直接照 Plan 接线会导致 MCP 调用全部被拒。因此修改 `engine.py`、`rule.py`、`persist.py`：CLI 把成功注册的 MCP 工具全名交给引擎；只有这些名字可以进入既有规则与模式判定；永久授权按工具全名保存。未注册工具仍然拒绝，原有黑名单、沙箱和 deny > ask > allow 优先级保持。
2. **适配现有 Tool 协议。** 仓库的 `name()` / `description()` / `parameters()` 是方法，`execute()` 接收 JSON 字符串，返回 `termcode.tool.Result`。转换只发生在 MCP 适配层；没有修改 tool、agent、tui、llm、config、conversation 或 prompt 包。
3. **每个 server 各自持有 AsyncExitStack。** SDK 的 AnyIO 取消域必须在所属任务中退出，不能从并发连接任务进入共享栈后，再由另一个任务 `wait_for(aclose())`。连接所属任务持续运行到退出信号，负责自己的退栈。进入 TUI 的等待有 30 秒总上限，清理半成品连接不会额外拖延启动；关闭总预算 5 秒，预留取消收尾时间。
4. **使用 SDK v2 的实际字段名。** Python 中为 `read_only_hint`、`input_schema`、`is_error`、`next_cursor`，协议线上仍为相应 camelCase。锁定安装的是 `mcp==2.3.0`，约束为 `mcp>=2.2.0,<3`、`httpx2>=2.5.0,<3`。新协议请求头由 SDK 组装；回退旧协议时遵守旧协议头部要求，不额外伪造新协议字段。依据：[官方客户端 API](https://py.sdk.modelcontextprotocol.io/api/mcp/client/)。
5. **确定性 server 与真实模型分开验证。** stdio 使用可独立运行的最小 JSON-RPC server 子进程，不依赖下载 Node 示例 server。HTTP 使用真实本地 HTTP 端点；可选的 HTTP tmux 场景没有运行，不勾选。真实模型验证另有单独记录。

配置示例见 [mcp-servers.example.yaml](../mcp/mcp-servers.example.yaml)。仅 env / headers 的值展开变量；覆盖后再校验、展开，避免被覆盖的用户配置产生无关凭据告警。工具列表支持分页；歧义命名被跳过，不让 registry 重名导致启动失败。

## 自动化验证

| 检查 | 实际结果 |
| --- | --- |
| Windows / Python 3.14 全量 pytest | 283 passed、2 skipped，78.73 秒；报告 `.cache/ch07-windows-tests.xml` |
| Linux / Python 3.12 全量 pytest | 285 passed，165.98 秒；报告 `.cache/ch07-linux-tests.xml`；RuntimeWarning 按错误处理 |
| `ruff format --check .` | 通过，71 个 Python 文件格式一致 |
| `ruff check .` | 通过 |
| `mypy src/termcode` | 通过，40 个源码文件；包括 MCP 子包 |
| 凭据检查 | 本章文档、示例、源码与测试未命中 token 前缀正则；示例未被 gitignore 忽略 |
| 变更范围 | provider / tool / agent / tui / config 源码零 diff；permission 的三处必要扩展如上 |
| 构建 | `uv --cache-dir .cache/uv build --offline` 成功生成 sdist 和 wheel；wheel 含 MCP 四个模块，sdist 含配置示例，不含本机配置、缓存或原始验收记录 |

Windows 的两项跳过是原有平台分支。沙箱内 Windows 的 asyncio 初始化停在回环套接字创建；最终测试在允许真实事件循环和子进程的环境中执行，没有将沙箱内的停滞记为通过。Linux 测试开启 Python 开发模式，并把 `RuntimeWarning` 升级为错误。

| Checklist 范围 | 实际验证证据 |
| --- | --- |
| 两层配置、完整覆盖、缺失与非法文件降级（AC1） | `test_layers_missing_and_complete_override`、`test_bad_file_does_not_hide_other_layer`、`test_empty_layers`；覆盖非法 UTF-8、YAML、顶层结构、用户目录获取失败 |
| 字段校验与变量展开（AC2/AC3） | `test_bad_server_is_skipped`、`test_expand_only_values_once_and_keep_file`、`test_home_failure_and_example`；保留 command / args / 名字字面量，告警去重，不修改原文件 |
| stdio、环境、列工具与清理（AC4/AC10） | `test_real_stdio_handshake_env_and_exit` 在新旧两协议下启动真实子进程、调用工具、确认进程退出；tmux 另有逐进程检查 |
| HTTP 与协议头（AC5） | `test_http_headers_protocol_and_close`：真实本地 HTTP；新协议检查 Authorization、MCP-Protocol-Version、Mcp-Method、Mcp-Name；旧协议验证 JSON-RPC 拒绝与 HTTP 404 均能回退 |
| 工具命名、schema 与内容适配（AC6/AC7） | `test_invalid_names`、`test_fields_and_safe_readonly`、`test_registry_namespace_schema_arguments_and_text`、`test_non_text_warns_once_and_remote_error`；image / audio / resource_link / embedded_resource 均不回灌；另测歧义命名与分页 |
| 错误、超时、取消与失败隔离（AC8/AC9） | `test_exception_timeout_redaction_and_cancel`、`test_empty_manager_and_parallel_failure_isolation`、`test_startup_timeout_closes_partial_connection`、`test_cancelling_startup_cleans_process`、`test_close_timeout_cancels_owner_task`；缩短测试超时后验证时间边界与清理 |
| 权限与永久授权（AC11） | `test_mcp_permission_exact_wildcard_persist_and_unknown_safety`：未注册 MCP 在 bypass 仍拒绝；注册后只读 Allow / 非只读 Ask / bypass Allow；通配 deny 优先；精确规则保存与新引擎重载；tmux 实测审批、重启和内置黑名单 |
| 双 provider 与既有能力（AC12/AC13） | `test_mcp_agent_real_providers_feedback_and_error_recovery` 通过两个官方 LLM SDK 验证 MCP 错误回灌、换工具恢复及工具定义；全量既有测试通过 |
| 凭据与规范（AC14/AC15） | 配置示例只通过 `${VAR}` 引用 MCP 凭据；YAML 异常不输出源文；受控连接日志只输出异常类型；MCP 调用异常脱敏；ruff / mypy / pytest 如上 |
| 旧协议自动回退（AC16） | stdio 明确拒绝、stdio 10 秒无响应、HTTP JSON-RPC 错误、HTTP 404 四条路径均经真实传输验证；`test_discover_no_response_falls_back_on_same_process` 记录同一进程的 discover → initialize → initialized |

## tmux 端到端记录

执行命令：`python tests/tmux_mcp_e2e.py`。真实运行 CLI 和 MCP server 子进程；模型响应由本地 SSE fixture 控制，用来稳定触发边界场景。

OpenAI / Anthropic 两协议均完成下表场景。共 8 次隔离 CLI 启动与退出，均退出码 0、前后终端模式一致；逐次检查两个 MCP 子进程均已退出。汇总：[ch07-tmux-results.json](../evidence/ch07-tmux-results.json)。

| Checklist 场景 | 两协议实际结果 | 本机证据（OpenAI 示例） |
| --- | --- | --- |
| 1 无 MCP 配置 | 正常进入 TUI、完成对话；请求只含 6 个内置工具，无 MCP 告警 | [终端](../evidence/tmux-ch07-openai-empty.txt) |
| 2 stdio server 接入 | 确认菜单选择允许本次，真实 echo 返回“你好 MCP”，模型继续回复；没有写入永久规则 | [审批](../evidence/tmux-ch07-openai-mcp-once-approval.txt)、[完成](../evidence/tmux-ch07-openai-mcp-once.txt) |
| 3 失败隔离 | 不存在的 command 告警，demo / old 均注册成功，能调用 | [启动诊断](../evidence/ch07-openai-mcp-mcp-stderr.txt) |
| 4 永久允许并重启 | 精确规则 `mcp__demo__echo` 写入本地配置；真正退出、重启 CLI 后直接返回 reloaded，无审批 | [永久审批](../evidence/tmux-ch07-openai-mcp-persist-approval.txt)、[重启](../evidence/tmux-ch07-openai-mcp-reload.txt) |
| 5 环境展开 | 未设置变量时告警但仍启动、可调用；设置后工具返回 expanded-in-tmux；未修改原配置 | [未定义](../evidence/ch07-openai-missing-mcp-stderr.txt)、[已展开](../evidence/tmux-ch07-openai-mcp-legacy-env.txt) |
| 6 退出清理 | `/exit` 后退出码 0、stty 恢复、逐 PID 断言进程不存在 | [退出](../evidence/tmux-ch07-openai-mcp-exit.txt)、汇总 JSON |
| 7 bypass 与黑名单 | 三次 Shift+Tab 到 bypass，旧 server 的非只读 echo 直接执行；内置危险 Bash 被黑名单拒绝并回灌 | [终端](../evidence/tmux-ch07-openai-mcp-bypass.txt) |
| 8 旧协议 | old 拒绝 discover 后握手成功，协议 2025-11-25；demo 为 2026-07-28；旧工具实际调用成功 | 启动诊断、已展开场景终端 |
| 额外取消场景 | 在旧工具审批时按 Esc，干净返回空闲；下一条对话收到 CANCEL_RESUMED | [恢复](../evidence/tmux-ch07-openai-mcp-cancel-resume.txt) |
| 9 HTTP，可选 | 真实本地 HTTP 集成测试已执行；没有额外运行 HTTP tmux 场景，保留未勾选 | `test_http_headers_protocol_and_close` |

Anthropic 使用相同证据文件名，把 `openai` 替换为 `anthropic` 即可。

测试中的危险命令仅用于验证执行前拒绝。独立 tmux 服务的 PATH 另放置无破坏的 `rm` 替身，并断言替身没有被执行。

最初测试脚本直接重定向 stderr，发现 Textual 本身也在该流绘制，导致终端画面为空；修正为保留真实终端属性、仅旁录 `[mcp]` 诊断行。另一次脚本只切换两次模式，实际停在 PLAN，已改为三次后重跑。这两次未完成的测试不计为通过。

## 真实模型观察

执行命令：`python tests/tmux_mcp_e2e.py --live`，使用项目已有 OpenAI / Anthropic 兼容模型配置，在隔离项目和真实 tmux 中发送中文请求。

- 两个模型均实际请求 `mcp__demo__echo`；界面出现默认模式的三选审批菜单。
- 选择“允许本次”后，server 的真实 `tools/call` 记录包含本次唯一文本标识；界面显示工具结果，模型随后回复。
- 两个会话退出码均为 0，退出前后终端模式一致，demo / old 的 PID 均已不存在。
- 逐次捕获检查配置密钥没有出现在终端或 MCP 诊断日志中。

证据：[汇总 JSON](../evidence/ch07-live-results.json)、[OpenAI 审批](../evidence/tmux-ch07-live-openai-approval.txt)、[OpenAI 完成](../evidence/tmux-ch07-live-openai-done.txt)、[Anthropic 审批](../evidence/tmux-ch07-live-anthropic-approval.txt)、[Anthropic 完成](../evidence/tmux-ch07-live-anthropic-done.txt)。

真实模型验证范围为 MCP 工具自主选择、审批、执行和续答；其它边界由确定性 tmux 和集成测试验证。原始终端记录位于已忽略的 `docs/evidence/`，本地可查看。
