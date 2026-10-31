# ch08 上下文管理开发与验收记录

验收日期：2026-10-31（Asia/Tokyo）。输入为本目录的 Spec、Plan、Tasks、Checklist 四份文档。

## 实现

- 新增 `termcode.compact` 九个模块：常量、会话状态、用量估算、工具结果落盘、摘要模板、恢复段、摘要重试和统一编排。
- 工具结果按 UTF-8 字节执行单条和聚合预算，完整结果原子落盘，保留稳定预览；失败保留原文，下轮重试。
- 自动、手动、紧急压缩复用摘要路径；包含九节模板、文件与工具恢复、近期原文配对、摘要自身超长重试和自动熔断。
- `SessionRuntime` 跨用户轮次保存账本、文件追踪、熔断和用量锚点；会话锁保护普通对话和手动压缩。
- Agent 在每次请求前管理上下文，成功读取文件后同步记录原始内容；紧急压缩后仅重试一次，不重复执行已完成工具。
- TUI 复用常驻 Agent，统一分发 `/exit`、`/plan`、`/do`、`/compact`，显示压缩开始、完成或失败提示，支持取消。
- 配置新增 `context_window`，更新示例、忽略目录、README 和 smoke 入口。

## 文档冲突的处理

以下选择以 Spec 的行为要求为主，并保留仓库已有功能：

1. 仓库原来是 `config.py` 和 `agent/__init__.py`。配置改为 `config/` 包，Agent 实现移到 `agent/agent.py`，保留原导入入口，已有调用仍可使用。
2. Plan / Tasks 同时写了“无需锁”和“全部加锁”。按 Spec N2 使用 `threading.RLock` 保护账本、文件追踪、熔断与 Conversation；普通对话和手动压缩使用同一把 `asyncio.Lock`。
3. T8 中用 `decide_once(..., kept)` 探测未见 ID 会提前冻结所有决策。实现使用同一临界区内的 `has_seen` 检查，未见项再执行真正的预算决策。
4. 三条各 80000 字节的结果都超过 F1 的 50000 字节阈值，因此三条全部落盘。独立聚合分支用五条各 50000 字节验证，只需落盘一条；不会为满足 AC2 的示例而跳过 F1。
5. I2 的消息数量需计入 T12a 要求的 assistant 衔接消息：`1 + len(recent) + (recent 首条为 user)`。摘要与恢复仍合并为一条 user 消息。
6. E1 使用 30 次用户对话，每次真实执行一次文件读取、再续答，共 60 次主对话请求；保留单次 run 最多 25 次迭代的既有保护。最终历史少于 20 条，自动摘要实际触发。
7. 自动阈值使用第 1 层处理后的消息重新估算，入口的 `estimated_token` 只作为 before 统计值。只有确实触发摘要时才发压缩事件，熔断或仅落盘时保持静默。
8. 摘要响应的 usage 不写入主对话锚点。历史替换成功后清零旧锚点，下一次主请求再用实际 usage 替换；同时覆盖取消恰好发生在提交时的边界。
9. E14 中摘要失败由 `AFTER_EMERGENCY.err` 报告；若摘要成功但主请求重试仍超长，则按 F26 由普通 `Event.err` 报告，不发起第二次紧急压缩，也不把已经成功的摘要误报为失败。
10. 示例中的 `Config.validate()`、`scripts/smoke.py`、`docs/python/ch08/` 与现有仓库不符，分别使用实际校验入口 `_from_dict`、`examples/smoke.py` 和本目录。
11. E12 复用项目已安装依赖的 Python 环境，直接运行真实 CLI；本次没有增加依赖。工作区包含本次开发变更，因此用 `git check-ignore .termcode/sessions/` 验证会话目录忽略规则。

## 已运行的检查

| 检查 | 结果 | 证据 |
|---|---|---|
| Windows 全量 pytest | 399 passed, 2 skipped | `docs/evidence/ch08-pytest.xml` |
| Linux 补验 Windows 跳过分支 | 5 passed | `docs/evidence/ch08-linux-platform.xml` |
| compact 单测 | 67 项通过 | `tests/compact/`，包含 50 线程并发 |
| 新增 Agent 集成测试 | 15 项通过 | `tests/agent/test_context.py` |
| 新增 SDK 错误测试 | 6 项通过 | `tests/llm/test_context_errors.py` |
| 新增 TUI 测试 | 14 项通过 | `tests/tui/test_context_commands.py` |
| mypy | 55 个源文件无错误 | 使用 `.cache/mypy-ch08-clean` 独立缓存 |
| ruff check | 源代码与本次新增、修改测试均通过 | 包含 import 排序检查 |
| ruff format --check | 指定的 29 个源文件已格式化 | Checklist B3 所列路径 |
| compileall / import / CLI help | 通过 | `python -m compileall -q src/termcode`、`python -m termcode --help` |
| 源码与文档引用用语检查 | 无目标短语命中 | B11 / BB1 对实际路径执行 |
| 首次落盘与冻结路径抽查 | 10 条各 60000 字节首次约 38.035ms；冻结后平均 0.052ms、最大 0.104ms | `docs/evidence/ch08-layer1-performance.json` |
| tmux 双协议 | Anthropic / OpenAI 均通过 | `docs/evidence/ch08-tmux-results.json` |
| tmux 真实模型 | 通过，退出码 0，终端恢复 | `docs/evidence/ch08-tmux-live-results.json` |
| tmux 同目录重启 | 两次退出码 0，生成两个不同 ID，目录均保留 | `docs/evidence/ch08-tmux-restart-results.json` |

Windows 跳过的两项分别受符号链接权限、文件名不允许 `*` 影响；对应 Linux 分支已实际补验。
Windows 沙箱会阻断 asyncio 的本机 socketpair，异步和 HTTP 测试在允许本机通信的环境运行。
原有 mypy SQLite 缓存损坏，改用独立缓存完成检查，没有删除原缓存。

主要复现命令：

```powershell
.venv/Scripts/python.exe -m pytest -q --basetemp .cache/pytest-ch08-release --timeout=45 --tb=short --junitxml=docs/evidence/ch08-pytest.xml
.venv/Scripts/python.exe -m mypy --cache-dir .cache/mypy-ch08-clean src/termcode
.venv/Scripts/python.exe -m ruff check src/termcode
```

```sh
# 在已安装项目依赖和 tmux 的 Linux / WSL 中执行
.venv-linux/bin/python tests/tmux_context_e2e.py
.venv-linux/bin/python tests/tmux_context_e2e.py --live
.venv-linux/bin/python tests/tmux_context_e2e.py --restart-only
```

## Checklist 逐项证据

下表“通过”指所列程序或场景已实际运行；原文中有冲突的断言按前述处理方式验收。

| 编号 | 结果 | 验证证据 |
|---|---|---|
| C1 | 通过 | 九个核心模块存在，pytest、编译与导入成功 |
| C2 | 通过 | `compact/const.py` 集中预算；`config/protocol_defaults.py` 独立定义协议默认值 |
| C3 | 通过 | `test_new_session_context`、随机源降级测试；两次 ID 不同且目录存在 |
| C4 | 通过 | `test_decide_once_freeze_kept_replaced_and_skip`；kept / replaced / skip 均检查 |
| C5 | 通过 | `test_auto_tracking_consecutive_budget`，失败序列 `[1,2,0,1,2,3]` |
| C6 | 通过 | `test_recovery_state_concurrent` 50 线程；快照修改不影响内部记录 |
| C7 | 通过 | `test_offload_single_result`、`test_offload_aggregate`、幂等与冻结测试 |
| C8 | 通过 | 注入 OSError；两次失败均保留原文、不记账，恢复写入后成功 |
| C9 | 通过 | ASCII、中文多行、中文长行预览；同时检查 20 行、2048 字节与四类标记 |
| C10 | 通过 | 九节模板与提取测试；`test_run_summary_basic` 检查第 6 节用户原文 |
| C11 | 通过 | 三段恢复、五文件上限、头部截断；七次真实读取的 Agent 集成测试 |
| C12 | 通过 | 相同快照与工具定义生成相同边界提示文本 |
| C13 | 通过 | `test_estimate_tokens_anchor`、工具参数计量、四字段 usage 求和 |
| C13a | 通过 | 166999 / 167000 / 167001 三个锚点及落盘后重新估算的阈值测试 |
| C14 | 通过 | Textual Pilot `/compact` 与 `/unknown`；普通 run 调用数为零 |
| C15 | 通过 | `run_force_compact` 返回前后估算，失败由 TUI 显示 |
| C16 | 通过 | 双 SDK 的超长、其他 400、500 共六种包装测试，保留原异常 cause |
| C17 | 通过 | YAML 映射 context_window；拒绝负数、布尔、字符串、小数与 null |
| C18 | 通过 | 两协议默认值、零、正数覆盖与未知协议保守默认 |
| I1 | 通过 | `test_replace_history_deep_copy`、None / 空列表清空测试 |
| I2 | 通过 | 摘要、恢复、近期消息重建；user 起点额外插入 assistant 衔接 |
| I3 | 通过 | 默认 / 计划模式下捕获 manage、恢复渲染和 Request，三个 defs 引用相同；schema 全量匹配 |
| I4 | 通过 | Agent 主请求 stream 尾 usage 写入 runtime |
| I4-bis | 通过 | 连续锚点依次为 1000、1500、2200；摘要 usage 不混入，历史重建后清零 |
| I5 | 通过 | 真实 ReadFile，原始中文 CRLF 字节一致；第二次请求前快照已可见 |
| I6 | 通过 | 紧急摘要后一次重试成功、二次 PTL、重估仍过大不重试三类测试 |
| I7 | 通过 | 注册表四项、未知命令提示、`/exit` / `/plan` / `/do` Pilot 与 tmux 场景 |
| I8 | 通过 | 手动低用量压缩、完成数字、失败不退出、取消后可再次压缩 |
| I9 | 通过 | CLI 注入 runtime；多 provider 选择 80000 后复用同一 Agent 和 runtime |
| I10 | 通过 | 示例 context_window 非零且能解析，已补充默认值注释 |
| I11 | 通过 | CLI 启动实际创建会话目录；退出后工具原文仍存在，另一个启动创建新 ID |
| I12 | 通过 | 慢主请求与并发手动压缩按 `run → compact` 顺序执行 |
| I13 | 通过 | 自动 BEFORE / AFTER 事件一对；25 次低用量用户轮次没有压缩事件 |
| I14 | 通过 | 紧急 BEFORE / AFTER 一对，摘要失败和主请求重试失败均检查 |
| I15 | 通过 | 四种状态文本、手动共用格式化函数；真实 Pilot 在摘要响应前已显示等待提示 |
| B1 | 通过 | compileall、模块导入、CLI help |
| B2 | 通过 | `ruff check src/termcode` |
| B3 | 通过 | 指定路径 `ruff format --check`，29 文件 |
| B4 | 通过 | ruff 的 I 规则已启用，compact 导入排序通过 |
| B5 | 通过 | compact 67 项测试 |
| B6 | 通过 | 账本、文件追踪、熔断并发测试包含在全量通过结果中 |
| B7 | 通过 | Conversation 旧测试与新增深拷贝、清空测试 |
| B8 | 通过 | 配置旧测试与新增窗口校验测试 |
| B9 | 通过 | Agent 原有测试与新增 15 项集成测试 |
| B10 | 通过 | 原有 TUI 测试与新增 14 项 Pilot 测试 |
| B11 | 通过 | 指定源码目录没有目标外部引用短语 |
| BB1 | 通过 | 本目录 Spec / Plan / Tasks 按目标短语检查无命中 |
| B12 | 通过 | 示例 YAML 经 safe_load 与 `_from_dict` 校验通过 |
| E1 | 通过 | 30 用户轮次、30 次真实文件工具、60 次主请求；触发摘要且最终历史小于 20 条 |
| E2 | 通过 | Agent 80000 字节结果测试；双协议 tmux 实际 ReadFile 落盘 80008 字节（含行号） |
| E3 | 通过 | 三条 80000 全落盘；五条 50000 的聚合分支最少只落盘一条 |
| E4 | 通过 | 同一结果重复六次，预览对象复用，kept 不翻转，不重写文件 |
| E5 | 通过 | 手动命令低于阈值仍摘要，无普通请求；Pilot、双协议 tmux 和真实模型均观察到提示 |
| E6 | 通过 | 紧急路径先落盘，摘要后清锚点，只重试一次；过大直接错误 |
| E7 | 通过 | 自动三次失败熔断、成功清零、PTL 耗尽计数；手动仍可成功 |
| E8 | 通过 | 七次真实 ReadFile 后恢复最新五个文件，顺序、截断和请求 schema 均匹配 |
| E9 | 通过 | 200000 / 128000 / 显式 80000、100000；多 provider 选择时更新窗口 |
| E10 | 通过 | 两个保留量下界同时满足，切在 tool 起点时前移至 assistant 工具调用 |
| E11 | 通过 | G、G-1、G-2、G-3 与后续 ceil(20%) 序列；丢光不发空请求，非 PTL 立即停止 |
| E12 | 通过 | 真实模型大文件、手动和自动压缩、命令、退出码 0、终端恢复及会话目录保留 |
| E13 | 通过 | Pilot 慢摘要响应前已显示自动状态；事件、完成数字与双协议 tmux 均验证 |
| E14 | 通过 | 紧急状态在摘要前显示，成功续答；摘要失败与二次 PTL 按各自错误路径显示 |

## tmux 观察记录

双协议确定性验收直接启动 `python -m termcode`，通过 tmux 键盘输入真实中文请求和命令，
真实执行 ReadFile，供应商响应由本地 HTTP/SSE 服务提供，并经过各自官方 SDK。
两协议均观察到完整工具原文落盘、预览回灌、手动/自动/紧急摘要、未知命令隔离、
计划/执行模式切换、退出码 0、终端模式恢复及落盘文件保留。

真实模型使用现有 Anthropic 兼容配置 `glm-5.3-flash`，仅在隔离的测试工作区设置
`context_window=80000`，未修改用户配置。已观察到：

- 真实读取约 80KB 文件并产生 80608 字节工具原文落盘。
- `/compact` 显示完整开始和完成提示；短会话加上恢复快照后估算值可能增加，这是固定恢复预算导致的正常结果。
- 连续读取 43701 字节文件的第 5 轮出现自动压缩完成，第 7 轮曾观察到 54044 → 52277，之后继续正常回答。
- 最终复验在第 5 轮读取后观察到自动摘要完成并续答，记录为 49812 → 52033；恢复段包含最多五个文件快照，替换后从零估算，因此前后数字不保证单调下降。
- 最终 `/exit` 正常退出，返回码 0，`stty` 前后完全一致，工具结果文件仍然存在。

验收脚本先后修正了屏幕提示滚出视口导致的计数失真，以及把用户输入中的代号误当成助手完成的风险。
未把只出现“正在压缩”或测试脚本失败标记为完整通过；修正断言后的完整复验通过，结果保存在 `ch08-tmux-live-results.json`。
