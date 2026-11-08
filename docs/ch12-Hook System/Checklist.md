# Hook 生命周期挂钩系统 Checklist

> 每一项通过运行代码或观察行为来验证,聚焦系统行为。

## 验收记录

- 权限规则和 Hook 单元测试覆盖 `tests/permission/test_matcher.py`、`tests/test_permission_engine.py`、`tests/hook/`。
- Agent 事件、拒绝结果回灌和计划模式提醒由 `tests/test_agent.py` 的 Hook 用例验证。会话重置、输入保留、`/clear`、`/resume` 和 `/exit` 由 `tests/agent/test_runtime.py` 与 `tests/tui/test_hooks.py` 验证。
- WSL 全量 `pytest` 通过 603 项。Windows 的 Hook 执行器专项测试通过 14 项，`ruff check src tests` 和 Hook 模块的 mypy 检查均通过。
- tmux 本地验收使用 `python tests/tmux_hooks_e2e.py`。脚本在隔离目录中启动真实 TermCode，使用本地 SSE 服务提供可复现的模型事件，并运行实际工具、Shell Hook、ruff 和 HTTP Hook。
- 九个本地场景均通过，进程退出码均为 0，终端设置均恢复。详情见 [验收结果](../evidence/ch12-results.json)。
- 已在隔离的 WSL tmux 工作目录中通过智谱 `glm-5.3-flash` 实测。进程退出码为 0，终端设置恢复。详见 [真实模型验收结果](../evidence/ch12-live-results.json)。

## 实现完整性

### 权限匹配器扩展

- [x] `permission.Matcher` Protocol 存在,四种实现(`ExactMatcher` / `GlobMatcher` / `RegexMatcher` / `NotMatcher`)各自可单独导入并运行(验证:`pytest tests/permission/test_matcher.py -v` 通过)
- [x] `permission.Rule` 持有 `matcher` 字段，`parse_rule` 能识别 `=` / `~` / `!` 前缀。验证见 `tests/permission/test_matcher.py` 和 `tests/test_permission_engine.py`。
- [x] `to_rule_set` 在 `parse_rule` 失败时输出 stderr 错误日志(验证:单测用 `capsys` 捕获含 `parse failed`)

### Hook 包

- [x] `termcode.hook` 包存在且可导入(验证:`python -c "import termcode.hook"` 不抛 ImportError)
- [x] 11 个 `Event` 成员全部声明且 `is_blocking` 仅对 `PRE_TOOL_USE` / `USER_PROMPT_SUBMIT` 返回 True(验证:`pytest tests/hook/test_event.py` 或集成在 `test_engine.py`)
- [x] `load(...)` 能解析合法 YAML 并构造 Engine(验证:`test_loader.py` 全部通过)
- [x] Loader 对字段缺失 / 枚举错 / async + 拦截事件冲突 / matcher 编译失败均报 stderr 并跳过该条(验证:对应 `test_loader.py` 子用例通过)
- [x] `Engine.dispatch` 按声明顺序执行 rule 且拦截后中断后续(验证:`test_engine.py::test_dispatch_blocking` 通过)
- [x] Executor 的 shell `returncode == 2` 触发 blocked、`returncode == 0` 放行、其它非 0 视为失败不拦截(验证:`test_executor.py::test_run_shell_*` 通过)
- [x] Executor 的 HTTP 在 body 含 `{"decision":"block","reason":"..."}` 时触发 blocked(验证:`test_executor.py::test_run_http_*` 通过)
- [x] Executor 的 prompt 动作通过 `ExecutionResult.prompt` 字段返回文本(验证:`test_executor.py::test_run_prompt` 通过)
- [x] Executor 的 subagent 动作仅 stderr 输出占位日志、不阻塞(验证:`test_executor.py::test_run_subagent` 通过)
- [x] only_once 状态在 `SessionRuntime` 上，`/clear` 重置，`/resume` 使用新的运行时集合。验证见 `tests/agent/test_runtime.py` 和 `tests/tui/test_hooks.py`。

### agent / tui 集成

- [x] `Agent.__init__` 接受 `hook_engine` 参数，Agent 与 TUI 共接入 11 个事件。验证见 `tests/test_agent.py` 和 `tests/tui/test_hooks.py`。
- [x] TUI `submit()` 在 UserPromptSubmit 拦截时保留输入和焦点，并在输入框下方显示原因。验证见 `tests/tui/test_hooks.py::test_submit_blocked_keeps_draft_and_focus`。
- [x] TUI `on_mount()` 末尾派发 SessionStart，`/clear`、`/resume`、`/exit` 派发 SessionEnd。验证见 `tests/tui/test_hooks.py::test_mount_clear_resume_and_exit_dispatch_once`。
- [x] `cli.main` 退出前兜底 SessionEnd(验证:cli 调用链审查)
- [x] `/hooks` 命令已注册并按事件分组显示规则。验证见 `tests/test_command_builtins.py` 和 `tests/tui/test_hooks.py`。
- [x] `pending_reminders` 在取出后清空。验证见 `tests/agent/test_runtime.py::test_reminders_and_only_once_reset`。

## 集成

- [x] `hook.Engine` 与 `permission.Matcher` 共用同一套匹配实现(验证:`termcode.hook` 包不重复实现 exact/regex/glob)
- [x] `hook.Engine` 接入 `Agent.run` 后所有现有 agent 测试不破坏(验证:`pytest tests/agent/ -v` 全过)
- [x] `hook.Engine` 接入 tui 后所有现有 tui 测试不破坏(验证:`pytest tests/tui/ -v` 全过)
- [x] PreToolUse 拦截结果当 tool_result 回灌后,LLM 视角看到的是 `is_error=True` 的 `ToolResult`,`content` 含 `[hook <name>] <reason>`(验证:`test_agent.py` 检查 `results[call_id]` 字段)
- [x] reminder 注入路径与 plan reminder 协同——同一轮 LLM 请求的 reminder 串同时含两类(验证:`test_agent.py` 中构造 plan 模式 + hook prompt 注入,断言 reminder 串包含两段)

## 编译与测试

- [x] 项目可导入无错误:`python -c "import termcode"`
- [x] 入口可启动:`python -m termcode --help` 正常输出
- [x] 所有单元测试通过:`pytest`
- [x] ruff 检查通过:`ruff check src tests`
- [x] (可选)类型检查:`mypy src/termcode/hook` 无 error

## 端到端场景(tmux 实跑)

每个场景在 tmux 中单独启动 TermCode 实例。以下结果以本地 SSE 场景为准。

### 场景 1:PreToolUse shell 拦截 write_file

**预置:** 在 `.termcode/hooks.yaml` 写一条 hook:
```yaml
hooks:
  - name: block-write
    event: PreToolUse
    if:
      all_of:
        - field: tool_name
          match: { type: exact, value: write_file }
    action:
      type: shell
      command: "echo blocked by hook >&2; exit 2"
```

**步骤:**
- [x] tmux 启动 `python -m termcode`
- [x] 给 LLM 输入"创建一个文件 hello.txt 内容是 hi"
- [x] LLM 应触发 write_file,工具被拦截
- [x] scrollback 内 tool_result 显示 `[hook block-write] blocked by hook`、文件未创建
- [x] LLM 收到反馈后调整回应,不死循环

### 场景 2:SessionStart prompt 注入

**预置:**
```yaml
hooks:
  - name: zh-cn-default
    event: SessionStart
    action:
      type: prompt
      text: "默认用 zh-CN 回复"
```

**步骤:**
- [x] tmux 重启 termcode
- [x] 立刻发一句英文输入"hi there"
- [x] 首次模型请求的 reminder 含 zh-CN 指令，后续请求清空该提醒。本地 SSE 场景返回了中文回复。

### 场景 3:PostToolUse async shell 后台 ruff format

**预置:**
```yaml
hooks:
  - name: ruff-after-write
    event: PostToolUse
    if:
      all_of:
        - field: tool_name
          match: { type: exact, value: write_file }
        - field: tool_input.path
          match: { type: glob, value: "**/*.py" }
        - field: is_error
          match: { type: exact, value: "False" }
    action:
      type: shell
      command: "ruff format \"$(jq -r .tool_input.path)\""
    async: true
    timeout: 5s
```

**步骤:**
- [x] tmux 启动 termcode
- [x] 让 LLM 写一个语法有效但排版不整齐的 Python 文件
- [x] LLM 完成写入后主对话立即进入下一轮,不停顿
- [x] 验证文件被 `ruff format` 格式化(可手动 `cat` 该文件)

### 场景 4:UserPromptSubmit 拦截 delete 关键字

**预置:**
```yaml
hooks:
  - name: warn-delete
    event: UserPromptSubmit
    if:
      all_of:
        - field: prompt
          match: { type: regex, value: "(?i)delete" }
    action:
      type: shell
      command: "echo \"用户消息含 delete 关键字\" >&2; exit 2"
```

**步骤:**
- [x] tmux 启动 termcode
- [x] 输入"请帮我 delete 那个文件"
- [x] 输入被拦截,scrollback 内显示 `[hook warn-delete] 用户消息含 delete 关键字`
- [x] 输入框内容仍在(被退回用户重新编辑)
- [x] LLM 端未收到这条 user 消息(不发起请求)

### 场景 5:Stop HTTP 通知

**预置:**
- 启动可记录 POST 请求的本地服务。验收脚本会在回环地址上自动启动该服务。
```yaml
hooks:
  - name: notify-stop
    event: Stop
    action:
      type: http
      url: "http://127.0.0.1:9999/done"
      method: POST
```

**步骤:**
- [x] tmux 启动 termcode
- [x] 让 LLM 简单回答一个问题后停止
- [x] echo server 收到一次 POST,body 含 `"event":"Stop"`

### 场景 6:only_once + PreUserMessage

**预置:**
```yaml
hooks:
  - name: first-turn
    event: PreUserMessage
    only_once: true
    action:
      type: shell
      command: "echo first-turn-fired >&2"
```

**步骤:**
- [x] tmux 启动 termcode
- [x] 第一轮发任意消息,stderr 出现 `first-turn-fired`
- [x] 第二轮发消息,stderr 没有再次出现
- [x] 执行 `/clear` 进新会话,再发消息,stderr 重新出现 `first-turn-fired`

### 场景 7:错误配置不阻断启动

**预置:** `hooks.yaml` 含一条非法 hook:
```yaml
hooks:
  - name: bad-async
    event: PreToolUse
    async: true
    action:
      type: shell
      command: "echo x"
  - name: good-hook
    event: SessionStart
    action:
      type: shell
      command: "echo ok"
```

**步骤:**
- [x] tmux 启动 termcode
- [x] termcode 启动期 stderr 打印 `hook "bad-async": async not allowed for blocking events, skipped`
- [x] termcode 仍然成功进入 idle 状态
- [x] `/hooks` 命令仅列出 `good-hook`、未列 `bad-async`

### 场景 8:`/hooks` 命令

**预置:** 一份包含 3 条合法 hook 的 `hooks.yaml`(任意 event 组合)

**步骤:**
- [x] tmux 启动 termcode
- [x] 输入 `/hooks` 回车
- [x] 输出按 event 分组,每条一行 `  <name>  <event>  <action.type>  [flags]`
- [x] 末尾显示 `Loaded from: .../hooks.yaml`

### 场景 9:端到端组合(AC17)

**预置:** `hooks.yaml` 包含场景 1、2、3、4 全部 hook

**步骤:**
- [x] tmux 启动 TermCode。
- [x] SessionStart 向首轮请求注入 zh-CN 提醒。
- [x] 输入“创建 hello.py，然后用 ruff 格式化”。
- [x] 本地 SSE 首先请求 `write_file` 写入 `hello.py`，收到拦截结果后改用 `demo.py`，随后后台 ruff 完成格式化。
- [x] 输入含 delete 的消息时被拦截，输入框保留原文。
- [x] 同一进程内没有卡顿或未捕获异常栈，`/hooks` 仍列出 4 条 Hook。

## 外部真实模型联调

- [x] 英文问候触发 SessionStart 提醒，智谱模型以“钩子已启用”开头用中文回复。[首轮回复](../evidence/tmux-ch12-live-model-reminder.txt)
- [x] 模型调用 `write_file` 创建 `hello.py`。Hook 返回 `[hook block-write] blocked by hook`，会话存档中的工具结果标记为错误。模型随后说明文件被拦截，`hello.py` 未创建。[拦截和续答](../evidence/tmux-ch12-live-model-block.txt)
- [x] 模型调用 `write_file` 将 `x=1` 写入 `demo.py`。后台 ruff 将文件格式化为 `x = 1`，`/hooks` 显示该规则带 `[async]` 标志。[终端结果](../evidence/tmux-ch12-live-verified.txt)
- [x] 含 `delete` 的输入被拒绝，草稿保留，会话存档只有前三条测试请求。[输入拦截](../evidence/tmux-ch12-live-blocked-input.txt)
