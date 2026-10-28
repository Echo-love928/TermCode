# TermCode

使用 Python 3.12+、Textual、Rich 与官方 SDK 构建的终端多轮对话助手。
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
- Anthropic 思考增量不展示、不保存，只有正文进入对话历史。
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
- 从发送起显示 `Imagining… (Ns)`，完成或失败后显示总耗时。
- PageUp / PageDown 翻看已完成内容，也可用鼠标滚动。
- `/exit` 或 Ctrl+C 退出；Ctrl+C 在生成过程中也会退出程序。
- 已完成内容保存在当前会话中；退出时将渲染后的记录写回普通终端供回看，
  不创建会话文件，下次启动历史为空。
- 请求失败后显示红色错误，可继续输入；不自动重试，失败轮次不进入后续上下文。
- 本版本只提供文本对话，不调用工具、不读取工作区、不编辑代码。

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
```

该脚本在独立测试目录创建临时配置，不覆盖项目的真实配置；启动真实 tmux 会话、
发送对话和键盘操作，检查退出码与前后终端模式，并把记录保存在 `docs/evidence/`。

Linux / WSL 中的真实端到端验收：

```sh
tmux new -s termcode
uv run termcode
```

按照 `docs/Checklist.md` 验收，并在 `docs/Acceptance.md` 记录实际证据。
没有 tmux 或真实服务配置时，不将相应条目标为通过。

`docs/evidence/` 中的验收原始记录由本机测试生成，可能包含工作目录等本机信息，
已列入 Git 忽略规则，不随源码上传；验收文档中的相关证据链接用于本地查看。

## 实现依据

功能与边界以 `docs/` 中的四份文档为准。开发进度见 `docs/Progress.md`。

- [OpenAI 流式响应](https://developers.openai.com/api/docs/guides/streaming-responses)
- [Claude 手动 extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)
- [Textual App API 与 inline 模式](https://textual.textualize.io/api/app/)

