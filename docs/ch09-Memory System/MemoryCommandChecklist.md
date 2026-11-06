# /memory 会话命令验收

日期：2026-11-06。

## 使用方式

重启 TermCode，在会话空闲时输入 `/memory`。结果直接追加到现有对话区，可使用 PageUp / PageDown 或鼠标回看。用户级在前、项目级在后，每条展示文件标识、标题和完整正文。

示例（人工测试记忆）：

```text
Memories (2 entries):

  1. [user] user_preference_background — 用户画像
     测试用户：28届计算机毕业生，学习 AI Agent 开发。

  2. [project] project_knowledge_stack — 项目技术栈
     项目使用 Python 和 Textual。
```

命令只读取已保存的本机笔记，每次重新读取，不等待后台整理器生成尚未落盘的内容。没有笔记时显示空状态说明。命令本身不请求模型、不修改记忆、不加入模型对话历史或会话存档；原有系统提示中的记忆索引注入行为不变。

## Checklist

- [x] 输入 `/memory` 后在会话中显示记忆条数、`[user]` / `[project]`、标题和正文。证据：[tmux 展示](../evidence/tmux-memory-command-populated.txt)。
- [x] 没有保存的记忆时显示 `Memories (0 entries):` 与操作说明，不创建 memory 目录。证据：[空状态](../evidence/tmux-memory-command-empty.txt)、`test_empty_memory_does_not_call_model_or_create_files`。
- [x] 修改笔记后再次输入命令，读取更新后的内容；查看不会改写文件或索引。证据：[刷新](../evidence/tmux-memory-command-refreshed.txt)、`test_memory_lists_full_notes_refreshes_and_preserves_chat`。
- [x] 记忆正文使用纯文本展示，Rich 标签不作为样式执行；已配置的密钥值沿用现有脱敏逻辑。证据：`test_memory_lists_full_notes_refreshes_and_preserves_chat`。
- [x] 损坏、类型无效或命名不合法的笔记不影响其他条目；读取失败有可恢复提示。证据：`test_list_notes_reads_full_body_and_skips_invalid_notes`、`test_memory_read_failure_is_visible_and_chat_stays_idle`。
- [x] 38×20 窄屏保持输入区可见；查看后可以继续正常聊天。证据：[窄屏](../evidence/tmux-memory-command-narrow.txt)、[继续聊天](../evidence/tmux-memory-command-chat.txt)。
- [x] tmux 中实际输入命令，观察到命令产生 0 次模型请求；继续普通对话产生 1 次本地 SSE 请求，存档不包含 `/memory`。退出码为 0，前后终端模式一致。证据：[tmux 结果](../evidence/memory-command-tmux-results.json)。
- [x] Windows 全量测试：`PYTHONUTF8=1 python -m pytest -q --timeout=45`，423 passed、2 skipped，95.63 秒。证据：[测试报告](../evidence/memory-command-all.xml)。
- [x] Linux 相关测试：`pytest tests/tui/test_memory_command.py tests/test_memory.py`，9 passed，16.06 秒。证据：[Linux 报告](../evidence/memory-command-linux.xml)。
- [x] `ruff check src/termcode tests` 与 `ruff format --check src/termcode tests` 通过；三个本次修改的源码文件通过 `mypy --follow-imports=silent` 检查。
- [ ] 全仓类型检查通过：仍有 11 条既有错误，位于 conversation、session、tui/resume，本次不将其标记为通过。

## 验证边界

UI 沿用现有终端文本与滚动结构。参考 ui-ux-pro-max 中适用于所有平台的空状态规则：空内容提供说明和后续操作，不使用空白界面。

验收在 Ubuntu WSL / tmux 3.2a 中实际运行 CLI，后端为本地 SSE 测试服务，使用人工构造的身份和项目记忆，没有读取真实用户画像或调用云端模型。可复跑：

```sh
.venv-linux/bin/python tests/tmux_memory_command_e2e.py
```

原始记录保存在已忽略的 `docs/evidence/`，仅在本机查看。
