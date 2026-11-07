# ch10 斜杠命令验收记录

日期：2026-11-07。逐项结果见 [Checklist](Checklist.md)。在 WSL 的 Python 3.12 环境运行正式 TermCode CLI，以 200×50 的真实 tmux 窗格操作。默认验收使用本地 HTTP/SSE 服务、合成文件和隔离的用户目录；脚本实际检查 UI、HTTP 请求、工具执行和会话 JSONL。未使用用户的记忆或会话文件。

## 自动化和回归

| 项目 | 实际结果 | 证据 |
| --- | --- | --- |
| 全量 pytest | 496 项通过 | WSL：`.venv-linux/bin/python -m pytest -q --tb=short -p no:cacheprovider`（最终代码，134.14 秒） |
| 注册和解析 | 12 条命令按字典序注册；重复主名、别名及同一命令的重复别名均被拒绝；隐藏命令可执行但不出现在帮助和补全；空白、参数及大小写边界符合规格 | [注册测试](../../tests/test_command_registry.py)、[解析测试](../../tests/test_command_dispatch.py)、[内置命令测试](../../tests/test_command_builtins.py) |
| 分发和会话 | 纯本地命令不追加历史、不触发模型请求；忙碌时只允许本地查询；异常输出脱敏；新存档失败时保留旧会话；退出取消后台记忆任务 | [TUI 测试](../../tests/test_slash_tui.py)、[压缩回归](../../tests/tui/test_context_commands.py) |
| 补全和数据接口 | 验证滚动、高亮、窄屏、无匹配、换行、快速键入；记忆仅列文件名；会话重置清除压缩状态和计数 | [补全测试](../../tests/test_completion.py)、[会话和记忆接口测试](../../tests/test_command_helpers.py) |
| 静态检查 | `ruff check .`、`ruff format --check .`、`mypy src/termcode` 和 `git diff --check` 均通过。`tui/view.py` 无差异；启动和未知命令提示只引用 `/help` | 本次工作区检查 |
| ch09 tmux 回归 | OpenAI 与 Anthropic 两种协议均实际通过重启、查找/取消恢复、选中会话并继续追加；两份旧存档各有 8 条对话消息 | [ch09 结果](../evidence/ch09-tmux-results.json) |
| `/memory` tmux 回归 | 空目录、两级文件名、新文件可见、正文不展开、38 列终端和后续对话通过；查询模型请求数为 0 | [记忆命令结果](../evidence/memory-command-tmux-results.json) |

## 本地 SSE 的 tmux 场景

复跑：`PYTHONPATH=src .venv-linux/bin/python tests/tmux_slash_e2e.py`。脚本只调整终端输出的文件描述符去向，让 Textual 的 inline 画面保留在 tmux，错误日志单独落盘。原始 [JSON 结果](../evidence/ch10-tmux-results.json) 记录了 31 个证据点、5 次本地 SSE 请求、正常退出和零行 stderr。

| 场景 | 观察与断言 | tmux 截取或存档验证 |
| --- | --- | --- |
| A 启动与帮助 | DEFAULT 徽章和输入框可见；帮助显示 12 行对齐的命令与描述 | [启动](../evidence/ch10-A1-start.txt)、[帮助](../evidence/ch10-A2-help.txt) |
| B 本地命令 | 状态依次显示 `Mode/Tokens/Tools/Memories/Model/Directory`；权限为 `default`；记忆只显示 `MEMORY.md`；会话显示 ID 和绝对路径；累计 `0 in / 0 out`，模型请求数 0 | [状态](../evidence/ch10-B1-status.txt)、[权限](../evidence/ch10-B2-permission.txt)、[记忆](../evidence/ch10-B3-memory.txt)、[会话及零用量](../evidence/ch10-B4-B5-session.txt) |
| C 自动补全 | 首字符 `/` 加载 12 项，首屏 8 行，向下滚动覆盖剩余项；`/s` 仅显示 `/session` 和 `/status`；上下键切换高亮；Enter 执行 `/status`，Tab 执行 `/session`；Esc 保留 `/s`，退格后菜单消失 | [首屏](../evidence/ch10-C1-first-eight.txt)、[滚动](../evidence/ch10-C1-scroll.txt)、[前缀](../evidence/ch10-C2-prefix.txt)、[高亮](../evidence/ch10-C3-highlight.txt)、[Enter](../evidence/ch10-C4-enter.txt)、[Esc](../evidence/ch10-C5-escape.txt)、[退格](../evidence/ch10-C6-backspace.txt)、[Tab](../evidence/ch10-C7-tab.txt) |
| D 状态修改 | `/plan` 切到 PLAN；输入自然语言请求后，TermCode 执行 `read_file` 读取样例，续答请求含真实工具结果 `SLASH_READ_42`；`/do` 切回 DEFAULT、流式回复且 JSONL 存入执行指令；`/compact` 显示进度和估算 token `14010 → 851`；`/clear` 清空界面及计数，存档目录由 1 个变为 2 个 | [计划](../evidence/ch10-D1-plan.txt)、[真实文件工具](../evidence/ch10-D1-read-file.txt)、[执行](../evidence/ch10-D2-do.txt)、[压缩进度](../evidence/ch10-D3-compact-progress.txt)、[压缩结果](../evidence/ch10-D3-compact-result.txt)、[清空](../evidence/ch10-D4-clear.txt) |
| E 审查 | `/review` 进入流式状态；助手回复完成，存档第一条消息为含“审查”的固定 user 文本；发送给模型的是新会话中的审查请求，没有附加外部上下文 | [流式](../evidence/ch10-E1-review-stream.txt)、[回复](../evidence/ch10-E1-review-reply.txt) |
| F 恢复 | 列表包含两个会话；向下选中旧会话，恢复最近压缩标记后的内容及 `14024 in / 30 out`；空回车行为见 G2 | [列表](../evidence/ch10-F1-resume-list.txt)、[恢复内容](../evidence/ch10-F1-resume-selected.txt)、[恢复用量](../evidence/ch10-F1-resume-usage.txt) |
| G 未命中及空输入 | `/foobar` 提示 `/help`；用量不变，模型请求数和 JSONL 字节不变；空回车、纯空格回车无新消息，之后的 `/permission` 仍可用 | [基线](../evidence/ch10-G1-baseline.txt)、[未知命令](../evidence/ch10-G1-unknown.txt)、[空输入](../evidence/ch10-G2-empty-and-whitespace.txt) |
| H 重复注册 | 在独立进程的启动入口重复注册 `/help`，真实 CLI 启动在打开界面前失败，包含 `RuntimeError: command conflict: help` | [启动错误](../evidence/ch10-H1-startup-conflict.txt) |
| 退出和日志 | `/exit` 后 tmux 会话消失，退出码 0，前后 `stty` 相同，stderr 零行；脚本关闭隔离的 tmux 服务 | [退出前画面](../evidence/ch10-exit-before.txt)、[JSON 结果](../evidence/ch10-tmux-results.json) |

D3 以完成提示里的上下文估算值验收压缩效果。按 Spec F19/N11，状态栏累计用量不会被压缩减少，只在 `/clear` 或切换会话时变化。旧版存档没有累计用量记录时按零恢复；新存档的用量记录独立于对话消息和 compact 标记。

## 已配置模型的补充验收

复跑：`PYTHONPATH=src .venv-linux/bin/python tests/tmux_slash_e2e.py --live`。只使用项目配置的首个模型，在隔离工作区读取合成样例，额外核验模型实际选择工具和生成回复。

- 自然语言请求：请只用 `read_file` 读取 `sample.py`，说明 `next_value` 的实际行为，并引用文件内的核对码。模型选择了 `read_file`，TermCode 执行工具，回复给出 `CH10_LIVE_CODE_285` 和 `n - 1` 的行为。[终端截取](../evidence/ch10-live-read-file.txt)
- 输入 `/review` 后，固定审查文本以 `user` 角色落盘，真实模型生成审查回复，指出 `next_value` 的 `n - 1` 与名称暗示的 `n + 1` 不符。验收工作区拒绝 shell、写入及搜索请求，样例文件字节未改变。[发起审查](../evidence/ch10-live-review-start.txt)、[终端回复](../evidence/ch10-live-review.txt)
- `/exit` 退出码为 0，终端模式恢复，stderr 为 0 行。[真实模型结果](../evidence/ch10-live-tmux-results.json) 中 `requests` 只计本地 SSE 测试服务收到的请求。
