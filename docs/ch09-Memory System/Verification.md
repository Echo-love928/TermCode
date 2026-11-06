# ch09 开发与验收记录

日期：2026-11-06。实现采用现有 Python / Textual 架构。

已实现三层 TERMCODE.md 与安全 @include、追加刷盘 JSONL、恢复列表及搜索、孤立工具截断、恢复压缩与超时提醒、30 天后台清理、两级记忆 CRUD / 索引 / 异步更新，以及 CLI 生命周期集成。

仓库与文档差异：提示模块位于 `src/termcode/prompt/`；历史替换使用 `replace_history()`，新增 `replace_messages` 兼容别名。回调在会话锁内执行以保持并发顺序；ch08 无变化的落盘预处理不再触发历史替换，避免每次请求都追加 compact 标记。恢复失败时保留当前会话，允许重试。记忆操作失败回滚本批次，防止产生未索引文件。

## 实际执行

- WSL：`.venv-linux/bin/python -m pytest -q --timeout=40`，418 passed。
- 最新运行中键盘交互及存储恢复相关回归：25 passed。
- Windows：`ruff check src/termcode tests`、`ruff format --check src/termcode tests`，exit 0。
- `git diff --check`：exit 0。
- 真实模型：`PYTHONPATH=src .venv-linux/bin/python tests/tmux_memory_live_e2e.py`，独立 tmux、隔离 HOME / 工作区、使用现有 Anthropic 协议配置；真实模型为 glm-5.3-flash。未修改用户真实记忆目录。
- 本地双协议：`tests/tmux_memory_e2e.py`，真实终端与 SDK，模型响应由本地 HTTP/SSE 替身提供。不能据此声称真实模型语义效果通过。
- 可选 mypy：Windows 与 WSL 的 mypy 2.4.0 均报告 INTERNAL ERROR；类型检查未通过验收。未为此修改工具依赖。

## 可审阅证据

- [真实模型结果](../evidence/ch09-live-results.json)
- [真实工具调用与笔记](../evidence/tmux-ch09-live-tool-memory.txt)
- [真实重启恢复](../evidence/tmux-ch09-live-resumed.txt)
- [双协议结果](../evidence/ch09-tmux-results.json)
- 测试入口：`tests/test_instructions_loader.py`、`test_session.py`、`test_memory.py`、`test_memory_agent.py`、`test_resume.py`。

## Checklist 逐项记录

编号按 Checklist.md 中勾选项的出现顺序。仅实际观察或测试覆盖的条目勾选；“部分验证”“未完整实跑”保持未勾选。完整验收仍有未执行场景，不宣称整份 Checklist 通过。

| 编号 | 检查项 | 实际结果 | 验证证据或未完成原因 |
|---|---|---|---|
| 1 | 包可正常导入 | 通过 | `import termcode` exit 0 |
| 2 | 所有单元测试通过 | 通过 | 全量 418 passed；最新交互与相关模块另 25 passed |
| 3 | lint 无告警 | 通过 | ruff check / format --check：exit 0，115 files already formatted |
| 4 | 格式一致 | 通过 | ruff check / format --check：exit 0，115 files already formatted |
| 5 | 无线程/协程竞态 | 通过 | `test_concurrent_history_and_archive_order`：12 线程、100 条顺序一致；`test_update_concurrent_and_failure`：8 个并发更新无丢失 |
| 6 | 三层加载优先级 | 部分验证 | `test_priority_missing_and_prompt` 验证三层拼接及提示注入；未在 tmux 中构造三层冲突 |
| 7 | 缺失文件静默 | 通过 | 指令加载测试及无指令工作区 CLI 冷启动 |
| 8 | @include 正常展开 | 通过 | 指令测试；真实模型 tmux 中 @include rules.md 的前缀生效 |
| 9 | @include 嵌套展开 | 通过 | `tests/test_instructions_loader.py`：嵌套、深度、环路、逃逸、二进制 |
| 10 | 5 层深度截断 | 通过 | `tests/test_instructions_loader.py`：嵌套、深度、环路、逃逸、二进制 |
| 11 | 环路检测 | 通过 | `tests/test_instructions_loader.py`：嵌套、深度、环路、逃逸、二进制 |
| 12 | 路径逃逸 | 通过 | `tests/test_instructions_loader.py`：嵌套、深度、环路、逃逸、二进制 |
| 13 | 二进制文件跳过 | 通过 | `tests/test_instructions_loader.py`：嵌套、深度、环路、逃逸、二进制 |
| 14 | 空指令不影响系统提示 | 通过 | `test_priority_missing_and_prompt`、既有 `test_prompt.py` |
| 15 | Session ID 新格式 | 通过 | `tests/compact/test_state.py` 新格式正则及随机源失败降级；真实 CLI 生成新目录 |
| 16 | JSONL 首行带 model | 通过 | `test_writer_tool_roundtrip_compact`；真实模型独立 tmux 进程及测试进程回读 JSONL，工具调用及结果配对 |
| 17 | 消息实时追加 | 通过 | `test_writer_tool_roundtrip_compact`；真实模型独立 tmux 进程及测试进程回读 JSONL，工具调用及结果配对 |
| 18 | 工具调用记录 | 通过 | `test_writer_tool_roundtrip_compact`；真实模型独立 tmux 进程及测试进程回读 JSONL，工具调用及结果配对 |
| 19 | 压缩标记写入 | 通过 | `test_writer_tool_roundtrip_compact`；`test_resume_over_budget_compacts_before_switch` |
| 20 | fsync 刷盘 | 通过 | `test_writer_tool_roundtrip_compact`；真实模型独立 tmux 进程及测试进程回读 JSONL，工具调用及结果配对 |
| 21 | /resume 命令路由 | 通过 | 真实模型 tmux：`ch09-live-results.json`、`tmux-ch09-live-resumed.txt`；Pilot 恢复测试 |
| 22 | 列表展示正确 | 部分验证 | Pilot 构造三个历史会话和一个当前会话，实际验证 4 项与搜索后 1 项；未逐字按 3 项场景复跑 |
| 23 | 上下键导航 | 通过 | `test_resume_search_cancel_select_append`：真实 Textual 按键和当前 writer 保持不变 |
| 24 | 搜索过滤 | 通过 | 真实模型 tmux：`ch09-live-results.json`、`tmux-ch09-live-resumed.txt`；Pilot 恢复测试 |
| 25 | Esc 取消 | 通过 | `test_resume_search_cancel_select_append`：真实 Textual 按键和当前 writer 保持不变 |
| 26 | Enter 恢复 | 通过 | 真实模型 tmux：`ch09-live-results.json`、`tmux-ch09-live-resumed.txt`；Pilot 恢复测试 |
| 27 | 坏行跳过 | 通过 | `test_bad_lines_orphans_and_partial_append`；`test_resume_stale_and_invalid` |
| 28 | 孤立工具调用截断 | 通过 | `test_bad_lines_orphans_and_partial_append`；`test_resume_stale_and_invalid` |
| 29 | Token 超限自动压缩 | 通过 | `test_resume_over_budget_compacts_before_switch`：真实恢复路径、替身摘要器、标记持久化；压缩算法由既有 ch08 测试验证 |
| 30 | 时间跨度提醒 | 通过 | `test_resume_stale_and_invalid`：7 小时前时间戳，恢复后追加暂停提醒 |
| 31 | 恢复后追加 | 通过 | 真实模型 tmux：`ch09-live-results.json`、`tmux-ch09-live-resumed.txt`；Pilot 恢复测试 |
| 32 | 旧格式不展示 | 通过 | `test_list_cleanup`：31 天、新会话、旧格式同时构造并检查目录 |
| 33 | 运行中不可 resume | 通过 | `test_timer_busy_guard_narrow_resize_and_ctrl_c`：流式期间通过 Pilot 实际键入 /resume，显示等待提示且不切换状态 |
| 34 | 过期清理 | 通过 | `test_list_cleanup`：31 天、新会话、旧格式同时构造并检查目录 |
| 35 | 新会话不被清理 | 通过 | `test_list_cleanup`：31 天、新会话、旧格式同时构造并检查目录 |
| 36 | 旧格式保留 | 通过 | `test_list_cleanup`：31 天、新会话、旧格式同时构造并检查目录 |
| 37 | 清理不阻塞启动 | 部分验证 | 实现为 create_task + to_thread；未实测大量过期目录时的启动耗时 |
| 38 | 显式记忆触发 | 通过 | 真实模型 tmux 生成 project_knowledge 笔记、frontmatter 与 MEMORY.md；`tmux-ch09-live-tool-memory.txt` |
| 39 | 每 5 轮自动触发 | 部分验证 | `test_trigger_five_and_explicit_snapshot` 验证第 5 轮触发；未在 tmux 连续 5 轮后检查新增笔记 |
| 40 | 项目级分类 | 通过 | 真实模型 tmux 生成 project_knowledge 笔记、frontmatter 与 MEMORY.md；`tmux-ch09-live-tool-memory.txt` |
| 41 | 索引更新 | 通过 | 真实模型 tmux 生成 project_knowledge 笔记、frontmatter 与 MEMORY.md；`tmux-ch09-live-tool-memory.txt` |
| 42 | 记忆注入系统提示 | 通过 | `test_index_order_truncate`、提示参数测试；双协议本地 SSE 验证下一轮系统提示含新索引 |
| 43 | 异步不阻塞 | 通过 | `test_trigger_five_and_explicit_snapshot`：记忆更新被 Event 阻塞时第 6 轮对话仍完成 |
| 44 | 更新失败静默 | 通过 | `test_update_concurrent_and_failure`：错误记日志不抛出；请求 tools 为空 |
| 45 | 索引截断 | 通过 | `test_index_order_truncate`：UTF-8 总字节不超过 25KB，含截断标记 |
| 46 | 笔记更新 | 通过 | `test_store_crud_and_path_boundary`：创建时间保留、更新时间变化、索引替换与删除 |
| 47 | 笔记删除 | 通过 | `test_store_crud_and_path_boundary`：创建时间保留、更新时间变化、索引替换与删除 |
| 48 | 无工具请求 | 通过 | `test_update_concurrent_and_failure`：错误记日志不抛出；请求 tools 为空 |
| 49 | `build_system_prompt` 向后兼容 | 通过 | 既有 prompt / conversation 测试回归；空参数提示与固定模块逐字一致 |
| 50 | Conversation 回调向后兼容 | 通过 | 既有 prompt / conversation 测试回归；空参数提示与固定模块逐字一致 |
| 51 | 完整启动流程 | 通过 | 真实模型 tmux：`ch09-live-results.json`、`tmux-ch09-live-resumed.txt`；Pilot 恢复测试 |
| 52 | /resume 完整流程 | 通过 | 真实模型 tmux：`ch09-live-results.json`、`tmux-ch09-live-resumed.txt`；Pilot 恢复测试 |
| 53 | 场景 1（首次冷启动） | 未完整实跑 | 冷启动及存档分别已覆盖；未严格按此条发送“你好”并用 jq 验证 |
| 54 | 场景 2（项目指令生效） | 未完整实跑 | 真实模型验证了项目指令前缀；三层冲突只做了加载单测 |
| 55 | 场景 3（@include 展开） | 通过（等价场景） | 真实 tmux 加载 @include rules.md，模型实际遵循“验收：”前缀 |
| 56 | 场景 4（会话存档 + 工具记录） | 通过（等价场景） | 真实模型 read_file sample.txt，JSONL 有 user / assistant+tool_calls / tool_results / assistant |
| 57 | 场景 5（/resume 完整流程） | 通过（等价场景） | 真实模型记住“蓝鲸7391”，退出、重启、搜索并恢复，继续对话引用代号及文件内容，原存档追加 |
| 58 | 场景 6（/resume 搜索过滤） | 通过 | 双协议 tmux 搜索、Esc 取消、重新打开并恢复；另有 Pilot 状态断言 |
| 59 | 场景 7（恢复坏行容错） | 未完整实跑 | 坏行恢复已由真实文件及 Pilot 测试验证；未在 tmux 中插入坏行 |
| 60 | 场景 8（崩溃恢复） | 未完整实跑 | 半行恢复已测试；未对 TermCode 进程实际 kill -9 |
| 61 | 场景 9（过期清理） | 未完整实跑 | 清理及旧格式保护已做真实文件测试；未在 tmux 启动流程中按该场景布置目录 |
| 62 | 场景 10（记忆积累与注入） | 未完整实跑 | 真实模型笔记创建、索引注入与跨进程恢复分别已验证；未在全新会话仅问“你好”观察偏好 |
| 63 | 场景 11（运行中不可 resume） | 未完整实跑 | 真实 Pilot 键盘测试通过；未单独在 tmux 的模型运行中输入 /resume |
| 64 | 场景 12（压缩后 JSONL 标记） | 未完整实跑 | 存档标记、最后标记加载和恢复压缩均有测试；未在 tmux 中构造小窗口自动摘要后重启 |
