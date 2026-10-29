# ch03 工具系统验收记录

验收日期：2026-10-29（Asia/Tokyo）。对应本目录的 Spec、Plan、Tasks 与 Checklist。

## 实现结果

已完成 T1–T18：六个工具与注册中心、协议无关的 Agent 单轮编排、双协议流式工具解析与结果回灌、会话历史、系统提示词、TUI 工具行及 CLI 接线。

同批工具按顺序执行，随后只续答一次；续答再次请求工具时提示达到上限，不执行第二批。命令超时会清理进程树；Windows 使用原生 API，Unix 使用进程组。文件操作使用 UTF-8，修改保留原始换行符。

文档中的两处语义取舍：命令非零退出按 Spec F9 和 Checklist AC12 标记 `is_error=True`，同时保留 stdout/stderr/退出码；glob/grep 的空结果按 Tasks T7/T8 返回正常的“无匹配/无命中”。验收中原有的 `docs/python/ch03/spec.md` 示例路径替换为实际存在的 `docs/ch03-ToolSystem/Spec.md`。

## 测试与环境

- Windows 原生 Python：全量 83 项测试通过；另执行工具及协议集成回归，均通过。
- Ubuntu / WSL、Python 3.12.16：全量 83 项测试通过。
- `ruff check .`、`ruff format --check .`、Windows `mypy src/termcode` 均通过。
- Ruff 格式检查排除 Markdown，避免新版本 Ruff 格式化需求文档中的示例代码；源码和测试全部检查。
- tmux 使用独立 socket、独立临时工作目录，未覆盖项目的真实配置。原始证据在被 Git 忽略的 `docs/evidence/` 中，本文件的链接用于本机查看。

## 真实模型 tmux 验收

执行 `.venv-linux/bin/python tests/tmux_tools_e2e.py --live`，启动的程序是 `python -m termcode`，通过 tmux 输入真实自然语言请求。

| 协议与配置 | 读文件并总结 | 同批写/改/执行/找/搜 | 三类错误与继续对话 | 退出与终端恢复 |
| --- | --- | --- | --- | --- |
| OpenAI 兼容端点，deepseek-flash | 通过 | 通过 | 通过 | 退出码 0，stty 前后一致 |
| Anthropic 兼容端点，glm-5.3-flash | 通过 | 通过 | 通过 | 退出码 0，stty 前后一致 |

实际观察：两个模型均调用 read_file 读取本章 Spec，最终总结包含六个工具、双协议与单轮续答；同批五次调用将临时文件由 alpha 写入并修改为 beta，`cat` 输出 beta，glob 找到文件，grep 返回第 1 行命中。随后读取不存在文件、修改不存在片段和 `exit 7` 分别得到可区分结果，模型解释失败原因，再回答“你能做什么”，会话继续正常。完整退出记录包含全部工具行和结果摘要。

证据：[真实模型结果](../evidence/ch03-live-results.json)、[OpenAI 完整终端记录](../evidence/tmux-ch03-live-openai-exit.txt)、[Anthropic 完整终端记录](../evidence/tmux-ch03-live-anthropic-exit.txt)、[真实模型执行日志](../evidence/ch03-live-run.log)。

## 可重复的本地协议 tmux 验收

执行 `.venv-linux/bin/python tests/tmux_tools_e2e.py`。真实 CLI 与官方 SDK 连接本地 SSE 测试服务，两协议均覆盖读取、同批五工具、三类错误、慢命令运行指示、继续对话、PageUp/PageDown 回看和退出。两会话退出码均为 0，终端模式均恢复。它验证确定的协议响应，不替代上一节真实模型结果。

证据：[本地结果及请求角色序列](../evidence/ch03-tmux-results.json)、[OpenAI 完整记录](../evidence/tmux-ch03-openai-exit.txt)、[Anthropic 完整记录](../evidence/tmux-ch03-anthropic-exit.txt)、[执行中指示](../evidence/tmux-ch03-tools-openai-running.txt)、[回看记录](../evidence/tmux-ch03-openai-history.txt)。

## Checklist 逐项证据

以下序号对应 Checklist 中从上到下的 27 个复选项，全部按实际执行结果核验。

| 序号 | 验收项目 | 结果与证据 |
| --- | --- | --- |
| 1 | 注册中心 | 通过：`test_registry_definitions_and_lookup`，6 条定义、固定注册顺序、查找与重复注册；协议测试抓取两次请求均含 6 个工具。 |
| 2 | read_file | 通过：`test_read_file_numbered_and_errors`；真实终端记录显示行号，不存在文件与目录返回错误。 |
| 3 | write_file | 通过：`test_write_create_overwrite_and_edit_counts` 验证嵌套创建、覆盖、空内容与磁盘字节；真实模型创建临时文件。 |
| 4 | edit_file | 通过：同一测试验证 0/1/2 处匹配、失败不写入及 CRLF 保留；真实模型完成 alpha → beta。 |
| 5 | bash 与超时 | 通过：`test_bash_output_exit_and_bounded_capture`、`test_bash_timeout_kills_child_before_side_effect`；stdout/stderr/退出码正确，超时子进程未产生延迟文件副作用，Windows 与 Linux 均运行。 |
| 6 | glob/grep | 通过：`test_glob_grep_and_limits`；验证全局字典序前 100 项与文件:行:内容；真实模型均实际调用。 |
| 7 | 分片工具调用 | 通过：`test_protocol_fragmented_batch_and_result_feedback`，使用真实 SDK/HTTP，工具名称及 JSON 分片、多调用交错、中文参数均正确恢复。 |
| 8 | 读取闭环 | 通过：两种真实模型均读取 Spec 后给出基于内容的总结，见完整终端记录。 |
| 9 | 单轮上限 | 通过：`test_agent_batch_order_errors_and_second_round_limit`，续答的写调用未执行，文件保持 one，仅两次模型请求，并显示上限提示。 |
| 10 | 工具行与摘要 | 通过：真实模型完整记录包含 `● name(...)`、`⎿` 与 `[truncated]`；本地界面测试检查正文/工具/摘要/最终答复顺序。 |
| 11 | 错误回灌与恢复 | 通过：两种真实模型均解释不存在文件、0 处匹配和退出码 7 后继续对话；`test_error_summary_is_visibly_red` 验证错误摘要样式。 |
| 12 | 双协议一致 | 通过：两种真实兼容端点运行同组四轮请求；另有双协议 SDK 请求体与 TUI 测试。 |
| 13 | 工具历史 | 通过：`test_agent_read_result_history_and_final`、`test_conversation_copies_tool_lists` 与协议抓包验证调用 ID、角色序列及续答携带结果。 |
| 14 | 界面响应 | 通过：tmux 慢命令显示 Running；`test_tui_running_responsive_and_secrets_redacted` 在工具等待时翻页、缩放，界面继续响应。 |
| 15 | scrollback 顺序 | 通过：tmux 翻页和退出完整记录；`test_tui_tools_error_recovery_and_transcript` 断言 preamble → 工具行 → 摘要 → 最终答复。 |
| 16 | 体量控制 | 通过：工具测试读取 2100 行和 1MB 单行、执行 1MB 输出命令、搜索超过 100 个命中及文件，均有截断标记；长行搜索说明未完整搜索。 |
| 17 | Agent 能力提示 | 通过：两个真实模型在错误后回答“你能做什么”，均提及读写改、命令、glob/grep。 |
| 18 | 启动 | 通过：四个最终 tmux 会话均由 `python -m termcode` 启动并进入 TUI。 |
| 19 | Ruff lint | 通过：`ruff check .` 输出 All checks passed。 |
| 20 | Ruff format | 通过：`ruff format --check .`，源码和测试均已格式化。 |
| 21 | Pytest | 通过：Windows 和 Linux 全量各 83 passed，含既有配置、会话、协议、CLI 与界面回归。 |
| 22 | Mypy | 通过：Windows `mypy src/termcode` 输出 25 source files 无问题。 |
| 23 | 密钥不回显 | 通过：真实 tmux 每次抓屏检查全部配置密钥未出现；本地测试检查 TEST_SECRET、思考内容不出现，并验证工具参数/结果中的密钥被遮盖。 |
| 24 | 场景 1 | 通过：真实 OpenAI 兼容端点读取本章 Spec 并总结，工具行、摘要、最终 Markdown 和 `/exit` 均观察到。 |
| 25 | 场景 2 | 通过：两种真实模型同批写、改、bash、glob、grep，磁盘最终内容为 beta。 |
| 26 | 场景 3 | 通过：真实模型 edit 不存在片段返回“匹配到 0 处”，后续正常对话成功。 |
| 27 | 场景 4 | 通过：真实 Anthropic 兼容配置重复同组场景，观察到相同行为。 |

## 复现

```sh
python -m pytest -v
python -m ruff check .
python -m ruff format --check .
python -m mypy src/termcode
# Linux / WSL，需 tmux
python tests/tmux_tools_e2e.py
python tests/tmux_tools_e2e.py --live
```

当前边界保持 Spec 的章节范围：固定六工具、不做自动连环调用、不增加权限确认或目录沙箱、会话不落盘。
