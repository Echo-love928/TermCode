# TermCode 本地验收报告

日期：2026-10-27（Asia/Tokyo）。依据 `Spec.md`、`Plan.md`、`Tasks.md`、`Checklist.md`。

## 结论与范围

- T1–T13 开发完成；Checklist 中 25/29 项通过本地检查，4 项保留待真实模型验证。
- Windows / Python 3.14.3：54 项自动化测试通过，0 失败、0 跳过。
- Ubuntu WSL / Python 3.12.16：54 项自动化测试通过，0 失败、0 跳过。
- Ubuntu 的真实 tmux 3.2a 中运行了 5 个端到端场景，全部正常退出，退出码均为 0；5 次 `stty -g` 的退出前后结果均一致。
- tmux 中启动的是实际 `python -m termcode`，输入通过真实终端按键发送。后端是本地 HTTP/SSE 测试服务，调用的是实际 Anthropic/OpenAI 官方 SDK。
- **没有调用云端模型。** 本地服务返回固定测试文本，不能证明模型能记住名字、遵循系统提示词或真正执行扩展思考。用户已明确选择“先完成开发和本地测试，云端验收待配置后进行”。这些验收没有标记为通过。
- 本期纯对话，不要求工具调用；协议测试确认请求不包含工具定义。

## 执行与证据

Windows：

```powershell
.venv\Scripts\python.exe -m pytest -q --junitxml=docs/evidence/pytest-windows.xml
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m mypy src/termcode
.venv\Scripts\python.exe -m compileall -q src
.venv\Scripts\uv.exe lock --check --offline --cache-dir .cache/uv
.venv\Scripts\uv.exe build --offline --cache-dir .cache/uv
```

Linux / WSL：

```sh
.venv-linux/bin/python -m pytest -q --junitxml=docs/evidence/pytest-linux.xml
.venv-linux/bin/python tests/tmux_e2e.py
```

测试使用项目内独立的 Windows 与 Linux 环境，避免两种系统共用同一个虚拟环境。
tmux 脚本在 `.cache/tmux-e2e/` 下创建独立配置，不覆盖 `.termcode/config.yaml`；
结束后只关闭本次创建的独立 tmux 服务，不影响用户已有会话。

- [Windows 测试报告](evidence/pytest-windows.xml)：54 passed，15.86s。
- [Linux 测试报告](evidence/pytest-linux.xml)：54 passed，58.48s。
- [tmux 场景结果与请求摘要](evidence/tmux-results.json)：5 个场景、11 次已到达本地服务的请求。
- [质量与构建检查](evidence/quality-checks.json)：命令、退出码及输出。

## Checklist 逐项记录

下表顺序与 Checklist 中的 29 个条目一致。“通过”仅指已执行的本地验证。

| 序号 | 条目 | 结果 | 实际结果与证据 |
| --- | --- | --- | --- |
| 1 | 配置加载 | 通过 | 示例 YAML 和多项配置解析通过；tmux 实际启动。`test_config.py`、`tmux-anthropic-start.txt`。 |
| 2 | 配置校验 | 通过 | 子进程运行缺配置、缺密钥、非法协议、错误 YAML 和空列表，均以 1 退出，无堆栈和密钥原文。`test_cli.py`。 |
| 3 | 单 provider 直进 | 通过 | 单项配置进入带小狗横幅和输入框的界面。`tmux-anthropic-start.txt`、`tmux-openai-start.txt`。 |
| 4 | 多 provider 选择 | 通过 | 方向键选中第二项，进入对话且状态栏显示 `second-openai`。`tmux-selection-start.txt`、`tmux-selection-selected.txt`。 |
| 5 | 内置系统提示词 | 待云端 | 本地 HTTP 已验证系统提示词和完整历史进入请求；尚未验证真实模型的角色与规则回答。`test_providers.py`。 |
| 6 | 扩展思考 | 待云端 | 本地请求含 `thinking.enabled`、2048 token 预算；测试思考增量没有出现在界面。真实 Claude 是否接受配置并启用思考尚未验证。 |
| 7 | 流式输出 | 通过 | 真实终端先看到原始 `**你好**` 文本，再看到完成结果。`tmux-anthropic-streaming.txt`。 |
| 8 | Markdown 定型 | 通过 | 结束后显示列表、代码块和强调内容，不保留 Markdown 围栏。`tmux-anthropic-markdown.txt`、`tmux-openai-markdown.txt`。 |
| 9 | 多行输入 | 通过 | tmux 中实际 Alt+Enter 换行、Enter 提交，请求中含两行中文，提交后输入框清空。`tmux_e2e.py` 内含请求断言；`test_keys.py` 与连续输入回归测试通过。 |
| 10 | 响应计时 | 通过 | 首增量前可见 `Imagining… (1s)`，完成后显示 `完成 · 2.6s`。`tmux-anthropic-waiting.txt`、`tmux-anthropic-markdown.txt`。 |
| 11 | 错误反馈 | 通过 | 本地服务返回 HTTP 401；显示认证失败，随后同一会话成功回复。错误块样式经测试确认是 `bold red`。`tmux-error-authentication.txt`、`tmux-error-recovered.txt`、`test_view.py`。 |
| 12 | 正常退出 | 通过 | `/exit` 与生成期间 Ctrl+C 均以 0 退出；终端模式还原。`tmux-results.json`、`tmux-ctrl-c-exit.txt`。 |
| 13 | 界面布局 | 通过 | 首屏包含 ASCII 小狗、名称版本、工作目录、提示行、带边框输入框、❯ 和左右状态信息。`tmux-anthropic-start.txt`。 |
| 14 | 跨协议一致交互 | 通过 | 同一 TUI 驱动两种 SDK，本地服务分别收到 `/v1/messages` 和 `/v1/chat/completions`。`tmux-results.json`。 |
| 15 | 多轮语义上下文 | 待云端 | 本地已验证历史按顺序携带、失败轮回滚和新会话为空；固定回复服务不能证明模型正确引用前文。 |
| 16 | 非阻塞流式 | 通过 | 等待时计时持续更新，可翻页、缩放和退出；Pilot 和 tmux 均已执行。`test_tui.py`、`tmux-anthropic-waiting.txt`、`tmux-ctrl-c-exit.txt`。 |
| 17 | 历史回看与退出保留 | 通过 | 连续多轮后按 PageUp 回看；退出后普通终端记录含首轮“小林”和最后一轮历史测试输入。`tmux-anthropic-history.txt`、`tmux-anthropic-exit.txt`。 |
| 18 | base_url 覆盖 | 通过 | 两种协议均通过配置连接本地自定义兼容地址，路径和请求参数符合预期。`test_providers.py`、`tmux-results.json`。 |
| 19 | 窗口自适应 | 通过 | tmux 将窗口从 100 列缩到 38 列；重新排版历史内容，输入与状态栏保持可见。`tmux-anthropic-narrow.txt`；Pilot 另验证 38×20 视口。 |
| 20 | 命令行启动 | 通过 | 实际 tmux 进程执行 `python -m termcode` 并完成对话。5 个会话正常退出。 |
| 21 | lint | 通过 | `ruff check .` 返回 0。见质量检查记录。 |
| 22 | 格式 | 通过 | `ruff format --check .` 返回 0。见质量检查记录。 |
| 23 | 测试 | 通过 | Windows 与 Linux 均为 54 passed，0 failures、0 errors、0 skipped。 |
| 24 | 类型检查 | 通过 | `mypy src/termcode`：15 个源文件无问题。 |
| 25 | 密钥不回显 | 通过 | 配置对象隐藏密钥字段值；错误不显示 SDK 响应体；所有 tmux 文本记录无测试密钥或思考标记。校验错误可显示字段名 `api_key`，不显示其值。 |
| 26 | Anthropic 多轮端到端 | 待云端 | 本地 tmux 跑通多轮、流式、计时、Markdown 和退出；模型能正确回答前文姓名尚未验证。 |
| 27 | OpenAI 流式端到端 | 本地通过 | tmux 使用实际 OpenAI SDK 接收本地 SSE，展示代码块并正常退出。`tmux-openai-markdown.txt`、`tmux-openai-exit.txt`。云端尚未调用。 |
| 28 | 多 provider 选择端到端 | 本地通过 | tmux 选第二项后，状态栏和实际请求都对应 OpenAI 配置，正常对话并退出。`tmux-selection-selected.txt`。 |
| 29 | 错误恢复端到端 | 本地通过 | 本地服务模拟错误密钥返回 401，随后恢复响应；原进程可继续对话。`tmux-error-recovered.txt`。云端错误密钥场景尚未调用。 |

## 待配置后的真实模型验收

1. 本地填写 `.termcode/config.yaml`，不在聊天或报告中提供密钥。
2. 在 tmux 中使用 Anthropic 配置验证 extended thinking 被真实服务接受、思考隐藏、多轮语义引用和系统提示词行为。
3. 使用 OpenAI 或兼容服务验证同样的流式交互，再验证真实认证错误与恢复。
4. 记录实际服务、模型、日期和观察结果，再更新上述待验项。

不支持固定手动思考预算的模型可能拒绝 `thinking.enabled`。本版本按 Plan 指定参数发送，
不会自动替换模型或切换成另一种思考模式；此时会显示可恢复错误。

## 开发边界与可复现性

- Windows 的自动化测试覆盖界面状态和输入，但没有将 Windows 原生终端的手工验收等同于 WSL/tmux 验收。
- SDK 依赖已锁定；旧式 Alt+Enter 兼容适配依赖 Textual 的输入解析扩展点，有真实字节序列和 tmux 回归验证。
- 构建只打包指定源码、文档、测试和配置示例；产物不包含 `.cache/`、虚拟环境或真实配置文件。
- 项目当前没有 Git 仓库，未创建提交；源码、锁文件、进度和证据均保存在工作区。
