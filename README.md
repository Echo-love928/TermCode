# TermCode

使用 Python 3.12+、Textual、Rich 与官方 SDK 构建的终端 AI 编程助手。
支持 Anthropic Messages 和 OpenAI Chat Completions 两种协议，包括配置自定义地址的兼容服务。

## 安装

在项目根目录执行：

```sh
uv sync
```

或者使用 Python 虚拟环境：

```sh
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# Linux / WSL
# source .venv/bin/activate
python -m pip install -e .
```

## 配置和启动

将 `.termcode/config.yaml.example` 复制为 `.termcode/config.yaml`，填写真实密钥和模型。

```yaml
providers:
  - name: my-provider
    protocol: openai
    model: YOUR_MODEL
    api_key: YOUR_API_KEY
    base_url: https://api.openai.com/v1
    thinking: false
```

- `name`、`protocol`、`model`、`api_key` 为必填非空字符串。
- `base_url` 可省略，使用对应服务的默认地址；填写时使用 API 根地址，勿填具体请求路径。
- `thinking` 可省略，默认 `false`。仅 Anthropic 生效，按文档使用固定 2048 token
  思考预算与 4096 token 总输出限制；须选用支持手动 extended thinking 的模型。
  模型不支持此参数时显示请求错误，不自动切换思考模式。OpenAI 协议忽略此字段。
- Anthropic 思考增量不展示、不保存；携带工具历史时不启用 thinking。
- 配置一项时直接进入对话，多项时先用方向键和 Enter 选择。
- 只读取当前工作目录下的 `.termcode/config.yaml`，无环境变量或命令行覆盖。
- 配置包含密钥，已加入 `.gitignore`；请勿提交真实配置。

```sh
uv run termcode
# 或激活安装后的虚拟环境
python -m termcode
```

## 横幅显示

启动横幅直接使用参考图中的图案和 TermCode 字标图片，保留原图光晕。
Windows 请使用 Windows Terminal 1.22 或更新版本；程序自动检测终端的图片能力。
不支持图片的终端会显示文字提示，聊天照常使用。图片随历史消息滚动，并随窗口尺寸缩放。
终端支持范围见 [textual-image 说明](https://github.com/lnqs/textual-image#supported-terminals)，
本次修改的验证证据见 [横幅验收记录](docs/BannerChecklist.md)。

## 交互

- Enter 发送，Alt+Enter 换行；生成时暂不接受下一轮提交。
- 收到增量立即显示纯文本；完成后用 Markdown 渲染，支持代码块、列表和强调。
- 从发送起显示 `Imagining… (Ns · 第 N 轮)`，完成或失败后显示总耗时。
- 状态栏累计展示输入/输出 token 用量；服务未返回用量时不增加。
- 工具执行时显示 `● name(参数)` 和 `Running…`；完成后保留工具行与缩进摘要，
  失败结果显示为红色，长结果带 `[truncated]` 标记。
- PageUp / PageDown 翻看已完成内容，也可用鼠标滚动。
- 生成或执行工具时，Esc / Ctrl+C 取消本轮并留在会话中；空闲时 Ctrl+C 退出。
  `/exit` 在空闲时退出。
- `/plan` 进入计划模式，只能读取和调查；`/do` 切回普通模式并立即按上文计划执行。
- 系统提示按七个固定模块拼装，预留自定义指令、Skill 和记忆空槽；环境信息单独采集。
  规划提醒通过临时消息按第 1、5、9…轮完整注入，其余轮精简，不写入会话历史。
- 已完成内容保存在当前会话中；退出时将渲染后的记录写回普通终端供回看，
  不创建会话文件，下次启动历史为空。
- 请求失败后显示红色错误，可继续输入；不自动重试。工具执行结果保留在上下文中，
  续答请求失败也不会重新执行已经完成的操作。
- 提供 `read_file`、`write_file`、`edit_file`、`bash`、`glob`、`grep` 六个工具。
  `edit_file` 只替换唯一匹配的原文；`bash` 使用当前平台 shell（Unix sh / Windows cmd），
  返回 stdout、stderr 与退出码，非零退出作为错误结果。
- 模型可跨多轮持续调用工具，直到完成任务；内置最多 25 轮，连续 3 轮只请求未知工具时停止。
  连续只读调用（read_file/glob/grep）并发执行，写入、修改和命令按顺序串行执行，
  工具结果始终按调用顺序回灌。取消、错误或触达上限后补齐历史，可继续对话。
- 工具超时默认为 30 秒；命令超时会清理子进程。读文件最多 2000 行或 256KB，
  命令分别保留最多 14KB stdout/stderr，glob/grep 最多 100 条结果。
  工具执行受下文权限系统约束，请在需要操作的项目目录运行。

## 开发与验证

```sh
uv sync --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src/termcode
```

自动化测试使用本地测试 HTTP 服务发送 SSE，覆盖两种真实 SDK、请求参数、历史传递、
密钥不回显、异常与中断清理，以及 Textual 的键盘输入和状态转换。
这些测试不调用云端模型，不替代真实供应商验收。

已安装 tmux 的 Linux / WSL 环境可运行可复现的本地端到端测试：

```sh
uv run python tests/tmux_e2e.py
# ch03 工具场景：本地 SSE
uv run python tests/tmux_tools_e2e.py
# ch03 工具场景：当前配置中的真实模型
uv run python tests/tmux_tools_e2e.py --live
# ch04 多轮循环、取消、错误恢复、计划模式：本地 SSE
uv run python tests/tmux_loop_e2e.py
# ch04 真实模型场景（会向当前配置的服务发送验收请求）
uv run python tests/tmux_loop_e2e.py --live
# ch05 系统提示、规划提醒、环境感知和非 git 目录
uv run python tests/tmux_prompt_e2e.py
uv run python tests/tmux_prompt_e2e.py --live
# ch06 权限：双协议九场景（含永久授权重启验证）及真实模型审批/取消
uv run python tests/tmux_permission_e2e.py
uv run python tests/tmux_permission_e2e.py --live
# ch07 MCP：双协议审批、重启、回退、环境展开和退出清理
uv run python tests/tmux_mcp_e2e.py
uv run python tests/tmux_mcp_e2e.py --live
# 连发两条消息，打印 input/output/cache_write/cache_read
uv run python examples/smoke.py --provider my-provider
# 独立冷缓存诊断：只发送人工测试文本，不带项目提示、环境或工具
uv run python examples/smoke.py --provider my-provider --check-cache
```

该脚本在独立测试目录创建临时配置，不覆盖项目的真实配置；启动真实 tmux 会话、
发送对话和键盘操作，检查退出码与前后终端模式，并把记录保存在 `docs/evidence/`。

smoke 用量中的 `cache_write_reported=false` 表示端点没有提供有效写入计数，
此时 `cache_write=0` 只是兼容缺省值。`--check-cache` 每次使用新的测试标识与足够长的
人工前缀，两轮间保持前缀完全相同。退出码 0 表示首轮写入与次轮读取均验证，
2 表示只验证了读取，1 表示未观察到命中；不将读取计数推算为写入计数。

Linux / WSL 中的真实端到端验收：

```sh
tmux new -s termcode
uv run termcode
```

ch03 按照 `docs/ch03-ToolSystem/Checklist.md` 验收，
实际结果记录在 `docs/ch03-ToolSystem/Acceptance.md`。
ch04 按 `docs/ch04-Agent Loop/Checklist.md` 验收，
实际结果与尚未完成的项目见 `docs/ch04-Agent Loop/Acceptance.md`。
ch05 按 `docs/ch05-System Prompt/Checklist.md` 验收，
实际结果与缓存服务支持情况见 `docs/ch05-System Prompt/Acceptance.md`。
没有 tmux 或真实服务配置时，不将相应条目标为通过。

`docs/evidence/` 中的验收原始记录由本机测试生成，可能包含工作目录等本机信息，
已列入 Git 忽略规则，不随源码上传；验收文档中的相关证据链接用于本地查看。

## 实现依据

功能与边界以 `docs/` 中的四份文档为准。开发进度见 `docs/Progress.md`。

- [OpenAI 流式响应](https://developers.openai.com/api/docs/guides/streaming-responses)
- [Claude 手动 extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)
- [Claude 缓存前缀与用量字段](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [OpenAI 前缀缓存与 cached_tokens](https://developers.openai.com/cookbook/examples/prompt_caching101)
- [Textual App API 与 inline 模式](https://textual.textualize.io/api/app/)



## 权限系统

状态栏显示当前模式，空闲时按 Shift+Tab 循环切换 default、acceptEdits、plan、
bypassPermissions。默认模式下写文件、改文件和执行命令需要确认；只读工具默认允许。
`/plan` 进入只读规划，`/do` 返回 default 并按计划执行。

审批菜单支持 ↑↓ / j/k 选择、回车或空格确认、数字键 1/2/3 直选。
Esc/Ctrl+C 取消当前轮，仍可继续会话。“永久允许”生成本次具体命令或路径的精确规则。

参考 `.termcode/settings.yaml.example` 配置权限。用户级 `~/.termcode/settings.yaml`、
项目级 `.termcode/settings.yaml`、本地级 `.termcode/settings.local.yaml` 的规则合并，
按 deny > ask > allow 判定；默认模式 `default_mode` 单独按本地 > 项目 > 用户选择。
只读命中 ask 时直接拒绝；永久 allow 不会覆盖已有 ask/deny。
项目规则可提交，本地授权文件已忽略。黑名单和项目路径边界在 bypass 下仍然生效。

完整实现与验收范围见 [ch06 验收记录](docs/ch06-Permission%20System/Acceptance.md)。

## MCP 外部工具

启动时读取用户级 `~/.termcode/config.yaml` 和项目级 `.termcode.yaml` 的
`mcp_servers`；同名 server 由项目级完整覆盖。供应商配置仍放在项目的
`.termcode/config.yaml`。MCP 配置示例见
[mcp-servers.example.yaml](docs/mcp/mcp-servers.example.yaml)。

```yaml
mcp_servers:
  demo:
    type: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-everything"]
  remote:
    type: http
    url: "https://mcp.example.com/mcp"
    headers:
      Authorization: "Bearer ${MCP_TOKEN}"
```

`env` 和 `headers` 的值支持 `${VAR}`，未定义变量展开为空并告警；
命令与参数不展开。配置改动需要重启。单个 server 的失败不会影响其它工具，
所有连接并发启动，启动最多等待 30 秒。退出时统一关闭会话及子进程。

工具名为 `mcp__<server>__<tool>`。默认模式下，远端明确标注只读的工具直接放行，
其它工具需要确认。“永久允许”按工具全名保存，不按远端参数限制范围。
权限规则支持精确名和 `mcp__demo__*` 通配名，deny / ask 仍优先于 allow。
MCP 工具复用规则、模式和审批；本地文件沙箱与 Bash 黑名单只作用于内置工具。

仅回灌文本内容；非文本内容会丢弃并按工具告警一次。调用超时和远端错误
作为错误结果回灌，模型可以继续处理。暂不支持热加载、重连及 OAuth 交互。

实现差异和逐项证据见 [ch07 验收记录](docs/ch07-MCP%20Server/Acceptance.md)。
