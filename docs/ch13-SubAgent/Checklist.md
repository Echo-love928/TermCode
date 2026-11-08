# SubAgent 机制 Checklist

> 每一项通过运行代码或观察行为来验证,聚焦系统行为。

## 验收记录

- 环境为 WSL Ubuntu、Python 3.12 和真实 tmux。完整 pytest 回归通过 622 项，覆盖角色权限、模型档位、供应商错误脱敏和后台审批焦点。Ruff lint、格式检查、mypy 和 CLI 帮助命令均通过。Wheel 已构建并核对三个内置角色文件。
- 真实模型使用项目已有智谱配置。Explore 返回 0 个 `test_` 函数，源码副本前后字节一致。Fork 返回任务 ID `task_bca097a440bb4e92`，从父对话提取出 `# TermCode fixture`，TaskGet 返回 `completed`，主 Agent 在回复中确认收到完成通知。[真实模型结果](../../.cache/tmux-e2e/termcode-e2e-7ffba24623/results.json) · [tmux 会话抓屏](../../.cache/tmux-e2e/termcode-e2e-7ffba24623/ch13-live/exit.txt)
- 本地 SSE 验收通过 16 个场景。在真实 tmux 中运行 CLI、权限审批、工具和 Hook，并核对发送给模型的工具结果与通知。验收使用隔离的配置及文件副本。[本地场景结果](../../.cache/tmux-e2e/termcode-e2e-a6df415ae7/results.json) · [ESC 后台审批抓屏](../../.cache/tmux-e2e/termcode-e2e-a6df415ae7/ch13-detach/esc-background-approval.txt)
- 可在 WSL 的仓库根目录运行 `uv run python tests/tmux_subagent_e2e.py` 复现本地场景，加 `--live` 运行真实模型场景。超时场景在测试进程中把移交阈值设为 5 秒，产品默认值为 120 秒。

## 实现完整性

### subagent 包

- [x] `termcode.subagent` 包存在且可导入(验证:`python -c "import termcode.subagent"`)
- [x] `Definition` 数据类包含 `name / description / tools / disallowed_tools / model / max_turns / permission_mode / dont_ask / background / system_prompt / file_path / source` 全部字段(验证:`pytest tests/test_subagents.py -k catalog -v`)
- [x] `parse_definition` 能正确解析合法 frontmatter + body,`permissionMode=dontAsk` 时 `dont_ask=True`(验证:`pytest tests/test_subagents.py -k 'invalid_definition or completion_events' -v`)
- [x] `parse_definition` 对 frontmatter 缺 name / description 抛错,model 非法 fallback 到 inherit 并 stderr 警告(验证:对应测试通过)
- [x] 内置 3 个文件(general-purpose / explore / plan)在 `builtin/` 目录下,`importlib.resources` 读取成功(验证:`pytest tests/test_subagents.py -k catalog -v`)
- [x] `load_catalog` 按 builtin → user → project 顺序加载,同名高优先级覆盖(验证:`pytest tests/test_subagents.py -k catalog -v`)
- [x] `Catalog.resolve("Explore")` 在三层覆盖场景下返回正确 `source`(验证:对应测试通过)
- [x] `Catalog.fork_definition()` 返回 `is_fork() is True` 的临时 `Definition`(验证:对应测试通过)

### tool 过滤多层防线

- [x] `tool.filter` 定义 `ALL_AGENT_DISALLOWED_TOOLS`、`CUSTOM_AGENT_DISALLOWED_TOOLS` 和 `ASYNC_AGENT_ALLOWED_TOOLS`。
- [x] `apply_agent_tool_filter` 按 spec F30 顺序应用五层过滤(验证:`pytest tests/test_subagents.py -k filter -v`)
- [x] 后台定义式子 Agent 的工具集剔除 Agent、TaskList、SendMessage 等元工具。Fork 按 F24 保留 Agent 的声明，执行时拦截。
- [x] MCP 工具(`mcp__` 前缀)在后台模式下被保留(验证:对应测试用例通过)

### agent 包扩展

- [x] `Agent.__init__` 接受 `system_prompt / max_turns / permission_mode / dont_ask / approval_upgrader / provider` 6 个新关键字参数且生效(验证:`pytest tests/test_subagents.py -k 'completion_events or model_override' -v`)
- [x] `build_forked_messages` 正确克隆父消息 + 处理悬空 tool_use + 追加 Boilerplate(验证:`pytest tests/test_subagents.py -k filter_and_fork -v`)
- [x] `is_fork_context` 能识别消息中含 `<fork_boilerplate>` 标签(验证:对应测试通过)
- [x] `Agent.run_to_completion` 能跑完一轮非交互循环,返回最后一条 assistant 文本(验证:`pytest tests/test_subagents.py -k completion_events -v`)
- [x] `run_to_completion` 触达 `max_turns` 时 raise `MaxTurnsReached`(验证:对应测试通过)
- [x] `dont_ask` 放行未命中规则时需要询问的调用，保留显式 Ask、Deny 和沙箱裁决。
- [x] `approval_upgrader` 回调在 Ask 决策时被命中(验证:对应测试通过)
- [x] `run_to_completion` 把 events 转发到外部 `asyncio.Queue`,Tool / Text / Approval 事件可被消费(验证:对应测试通过)

### Agent 工具

- [x] `AgentTool` 构造后 `name = "Agent"`,`parameters()` 含 `prompt / description / subagent_type / model / run_in_background / name` 字段(验证:`pytest tests/test_subagents.py -k agent_tool_foreground -v`)
- [x] `AgentTool.execute` 缺少 prompt 时返回错误(验证:对应测试通过)
- [x] `AgentTool.execute` 未知 `subagent_type` 时返回错误(验证:对应测试通过)
- [x] `AgentTool.execute` 定义式 subagent 调用走前台 `run_to_completion`,返回 final_text(验证:对应测试通过)
- [x] `AgentTool.execute` `run_in_background=True` 时返回 `{"task_id":"...","status":"async_launched"}` JSON(验证:对应测试通过)
- [x] `AgentTool.execute` 在子 Agent context 内被再次调用时拦截(验证:嵌套阻断测试通过)
- [x] `AgentTool.execute` 检测到 conv 含 fork boilerplate 时拦截(验证:对应测试通过)
- [x] `AgentTool.execute` `enable_subagent_background=False` 时,`run_in_background=True` 与 fork 路径报错(验证:对应测试通过)

### task 包

- [x] `task.Manager.launch` 起协程跑 `run_to_completion`,跑完写 `status=COMPLETED`,push done(验证:`pytest tests/test_subagents.py -k background_task_tools -v`)
- [x] `task.Manager.launch` 协程内异常时,`status=FAILED`,`err` 含异常信息,主程序不崩(验证:对应测试通过)
- [x] `task.Manager.stop` 触发 `bt.handle.cancel()`,协程退出后 `status=CANCELLED`(验证:对应测试通过)
- [x] `task.Manager.send_message` 在已 completed 的任务上重新跑动,新 user 消息追加到 conv(验证:对应测试通过)
- [x] `task.Manager._by_name` 后启动覆盖前,`get(_by_name[name])` 返回最新 task(验证:对应测试通过)
- [x] `subscribe_done()` 返回的队列在 task 完成时收到 id(验证:对应测试通过)

### 4 个 task 工具

- [x] `TaskListTool` 返回当前所有任务的 JSON 列表(验证:`pytest tests/test_subagents.py -k background_task_tools -v`)
- [x] `TaskGetTool` 返回指定任务的完整字段;未知 id 返回 `is_error=True`(验证:对应测试通过)
- [x] `TaskStopTool` 调用 `manager.stop`,返回成功 JSON(验证:对应测试通过)
- [x] `SendMessageTool` 调用 `manager.send_message`,返回 resumed JSON(验证:对应测试通过)
- [x] 4 个工具都实现 `is_system` 属性,`is_system is True`(验证:工具列表过滤时它们对子 Agent 仍可见——实际上 ASYNC 白名单优先,在后台子 Agent 中**仍然不可见**;对前台定义式子 Agent 通过 `ALL_AGENT_DISALLOWED` 不在其中)

### TUI 集成

- [x] `TermCodeApp` 持有 `task_mgr` 与 `subagent_catalog` 字段(验证:`python -c "import termcode.tui.app"`)
- [x] `on_mount` 启动 `_consume_task_done` 协程,任务完成时把 `<task-notification>` 注入 `runtime.pending_reminders`(验证:`pytest python tests/tmux_subagent_e2e.py`)
- [x] `skills/executor.py` 的 Skill fork 复用子会话构造和 `Agent.run_to_completion`，既有 Skill 测试通过。
- [x] `cli.main` 注册 4 个 task 工具 + 1 个 Agent 工具,`subagent_catalog` 与 `task_mgr` 传给 `TermCodeApp`(验证:`python -m termcode --help` 不报错)
- [x] `Config` 新增 `enable_subagent_background` 字段(验证:`pytest tests/test_config.py -v`)

## 集成

- [x] `subagent.Catalog` 与 `tool.apply_agent_tool_filter` 协同工作:`resolve` 拿到 def,过滤函数按 `def.source / background / tools / disallowed_tools` 收窄(验证:`tests/test_subagents.py` 通过)
- [x] Agent 工具的前台调用与现有 ch11 Skill fork 路径不互相干扰(验证:skills 包测试通过 + 手动 tmux 验证一个 inline skill 与一个 Agent 工具调用)
- [x] Hook 引擎在子 Agent 内仍生效(PreToolUse / PostToolUse 在 `run_to_completion` 内被调用)(验证:hook 包测试 + 子 Agent 跑动手动断言 hook 触发)
- [x] 主 Agent 工具列表里仍含 `Agent` + `TaskList` + `TaskGet` + `TaskStop` + `SendMessage` 共 5 个新工具,数量稳定(验证:工具数计数测试)

## 编译与测试

- [x] 项目可启动:`python -m termcode --help`
- [x] lint 通过:`ruff check src/termcode/`
- [x] 格式化通过:`ruff format --check src/termcode/`
- [x] 类型检查通过(可选):`mypy src/termcode/`
- [x] 所有单元测试通过:`pytest tests/`

## 端到端场景(tmux 实跑)

每个场景在隔离的 tmux 会话中运行。相关场景可复用同一个 TermCode 实例以验证上下文、任务状态及重启前后的行为。

### 场景 1:定义式子 Agent(Explore)前台同步

**预置:** 从仓库根目录运行验收脚本，由脚本准备只读场景的源码副本。

**步骤:**
- [x] tmux 启动 termcode:`tmux new-session -d -s ch13 -x 200 -y 50 "uv run termcode"`
- [x] 给 LLM 输入:「用 Explore 子 Agent 找出 `src/termcode/permission` 包下所有以 `test_` 开头的函数,只统计数量,不要修改任何文件」
- [x] LLM 应触发 Agent 工具,`subagent_type="Explore"`,`run_in_background` 未设
- [x] scrollback 内出现 `● Agent(...)` 工具行,几秒后 Result 行展示子 Agent 的最终文本(含 `test_*` 函数数量)
- [x] tmux 抓屏(`tmux capture-pane -p -t ch13`)断言:输出包含 `Agent(` 工具行 + 数量数字
- [x] 验证权限包源码副本在只读测试前后字节一致。

### 场景 2:Fork 子 Agent 后台执行

**预置:** 无。

**步骤:**
- [x] tmux 启动 termcode
- [x] 第一轮读取 README，给 Fork 准备上下文。
- [x] 第二轮:「Fork 出去一个子 Agent,统计这个项目里 Python 文件总行数(不指定 subagent_type)」
- [x] LLM 应触发 Agent 工具,`subagent_type` 留空 → Fork 路径
- [x] tool_result 应立即返回 `{"task_id":"task_xxx","status":"async_launched"}`
- [x] 主对话立刻可以继续(输入 `/status` 应能响应)
- [x] 等待任务完成，主对话下一次模型请求的 reminder 区出现 `<task-notification>` 块,含 Result(行数统计)
- [x] 用 LLM 验证:「主 Agent,你刚刚有没有收到 task-notification?显示一下」

### 场景 3:主 Agent 用 TaskList / TaskGet 查询

**预置:** 接场景 2 之后,或者重启 termcode 后先 launch 一个长跑任务。

**步骤:**
- [x] 调用一个会跑较久的子 Agent:「用 `run_in_background=True`,让一个 general-purpose 子 Agent 读取文件并运行一个可取消的长任务」
- [x] 主 Agent 立即返回 task_id
- [x] 输入:「调 TaskList 看现在有什么后台任务」
- [x] LLM 调 TaskList 工具,scrollback 显示 task 列表 JSON 含 id / name / status=running / tool_count
- [x] 输入:「调 TaskGet 看这个任务详情」
- [x] LLM 调 TaskGet,显示完整字段含 start_time / tool_count / last_activity 等
- [x] 等几秒后:「再调 TaskGet 一次」
- [x] 验证 status 变化或 `tool_count` 增长

### 场景 4:TaskStop 取消任务

**步骤:**
- [x] 同场景 3 起一个 long-running 任务,拿到 task_id
- [x] 立刻输入:「调 TaskStop 把刚才那个任务停掉」
- [x] LLM 调 TaskStop 工具
- [x] 几秒后:`TaskGet` 应显示 `status=cancelled`
- [x] 主对话下次 turn 的 reminder 区出现 task-notification 含 `status=cancelled`

### 场景 5:权限决策 - dontAsk 兜底

**预置:** 创建项目级自定义 agent:
```
.termcode/agents/auto-bash.md
---
name: auto-bash
description: 自动批准 Bash 调用的测试 Agent
permissionMode: dontAsk
maxTurns: 5
---

你是一个测试 Agent。当用户让你跑命令时,直接用 Bash 工具跑,不要询问。
```

**步骤:**
- [x] tmux 启动 termcode(权限模式 default)
- [x] 输入:「用 auto-bash 子 Agent 跑 `echo hello-from-subagent`」
- [x] LLM 调 Agent 工具 `subagent_type=auto-bash`
- [x] 子 Agent 内部调 bash,**不应该弹出审批弹窗**
- [x] tool_result 含 `hello-from-subagent` 文本

### 场景 6:权限决策 - 升级到主 TUI

**预置:** 创建一个不含 dontAsk 的子 Agent:
```
.termcode/agents/ask-bash.md
---
name: ask-bash
description: 默认权限模式的测试 Agent
maxTurns: 5
---

你是一个测试 Agent。当用户让你跑命令时,直接用 Bash 工具跑。
```

**步骤:**
- [x] tmux 启动 termcode(权限模式 default,未预先批准 echo)
- [x] 输入:「用 ask-bash 子 Agent 跑 `echo from-ask-bash`」
- [x] LLM 调 Agent 工具 `subagent_type=ask-bash`
- [x] 子 Agent 调 bash 时,**主 TUI 应该弹出审批弹窗**(本期通过子 Agent 的 ApprovalRequest 直接 emit;`upgrade_approval` 默认返回 `(_, False)` 走默认路径,Approval 由 inline 路径 emit 到 TUI)
- [x] 用户选 Allow Once → 子 Agent 继续 → tool_result 含 `from-ask-bash`

### 场景 7:嵌套阻断 - 定义式子 Agent 看不到 Agent 工具

**预置:** 无。

**步骤:**
- [x] tmux 启动 termcode
- [x] 输入:「用 Explore 子 Agent。Explore 内部应该尝试再调用 Agent 工具(比如 prompt 写成『再调用一个 Plan 子 Agent』)」
- [x] Explore 子 Agent 跑动期间,因为工具列表里没有 Agent 工具,LLM 应该报告「无法调用 Agent」或自己直接做
- [x] tool_result 不应包含「Agent 工具未注册」一类错误——因为它根本看不到这个工具(被 `ALL_AGENT_DISALLOWED_TOOLS` 剔除)

### 场景 8:嵌套阻断 - Fork 子 Agent 调 Agent 工具被拦截

**预置:** 无。

**步骤:**
- [x] tmux 启动 termcode
- [x] 输入:「Fork 一个子 Agent,prompt 写『再 fork 一个子 Agent 阅读 README.md』」
- [x] 主 Agent Fork 出去后立即返回 task_id
- [x] 等几秒,task-notification 显示子 Agent Result 含「Fork 子 Agent 不能再启动 Agent」错误回灌后的处理结果(或子 Agent 自行调整不再尝试)
- [x] 调 TaskGet 看子 Agent Result;或 TaskList 看 last_activity

### 场景 9:SendMessage 续派任务

**预置:** 无。

**步骤:**
- [x] tmux 启动 termcode
- [x] 输入「用 `run_in_background=True name=worker1` 起一个 general-purpose 子 Agent，读取 README 第一行」。
- [x] 主 Agent 收到 task_id，第一次完成通知包含 README 首行。
- [x] 输入「用 SendMessage 给 worker1 续派读取 README 第二行的任务」。
- [x] LLM 调 SendMessage 工具,`manager.send_message` 重新激活 worker1
- [x] 第二次完成通知包含 README 第二行，任务 ID 和原对话历史保留。

### 场景 10:超时自动切后台

**预置:** 临时把 `AUTO_BACKGROUND_SECONDS` 改成 5 秒(代码常量调小做测试,或加配置项)。

**步骤:**
- [x] tmux 启动 termcode
- [x] 请求 general-purpose 子 Agent 执行 `sleep 8` 后回复 hello。
- [x] Agent 工具前台等 5 秒后超时,tool_result 返回 `{"task_id":"...","status":"timed_out_to_background"}`
- [x] 主对话可以继续接收输入
- [x] 等待命令结束，主对话收到含 hello 的完成通知。

> 测试完恢复 `AUTO_BACKGROUND_SECONDS=120`

### 场景 10.1 ESC 手动转后台

- [x] 在前台子 Agent 的 bash 审批框出现时按 ESC，Agent 工具返回 `detached_to_background`。
- [x] 主 Agent 完成本轮回复后，待审批请求重新显示为后台审批。
- [x] 按「允许本次」后同一子会话继续执行，TaskGet 显示 `completed` 并包含最终文本。

### 场景 11.5:全新自定义子 Agent 端到端

> 验证项目级自定义 Agent 文件被加载、resolve 命中、frontmatter 全字段生效、system_prompt 注入到子 Agent。
> 与场景 5 / 6 / 11 区别:那三条聚焦权限 / 覆盖语义,本条验证"全新角色"作为新增能力。

**预置:** 创建 `.termcode/agents/wc-counter.md`:
```yaml
---
name: wc-counter
description: 行数统计专家,只用 wc -l 计行,然后总结
disallowedTools:
  - write_file
  - edit_file
permissionMode: dontAsk
maxTurns: 5
---

你是一个专门统计代码行数的 Agent。
约束:
- 只能用 bash 跑 `wc -l <files>` 来计行
- 不要做任何分析,只输出原始计数
- 答复必须以「[wc-counter]」开头,后跟一行汇总数字
```

**步骤:**
- [x] tmux 启动 termcode
- [x] 输入:「用 Agent 工具调 `subagent_type=wc-counter`,任务: 统计 `README.md` 和 `src/termcode/cli.py` 的行数」
- [x] 主 Agent 触发审批后选「允许本次」(主 Agent 调 Agent 工具自身的权限)
- [x] 子 Agent 跑动,**不应弹任何审批框**(验证 `dont_ask` 生效)
- [x] tool_result 内容以 `[wc-counter]` 开头,含 wc 计数(验证 system_prompt 注入生效)
- [x] 子 Agent 工具列表内不含 `write_file` / `edit_file`(验证 disallowedTools 生效)
- [x] 子 Agent 最多 5 轮即终止(验证 maxTurns 生效;实际单轮就完事)

### 场景 11.6:自定义 Agent 字段错误降级

**预置:** 创建 `.termcode/agents/bad.md` 含非法字段:
```yaml
---
name: bad
description: 字段错误测试
model: gpt-4   # 不在 inherit/haiku/sonnet/opus 中
permissionMode: weirdMode
---

body
```

**步骤:**
- [x] tmux 启动 termcode
- [x] stderr(启动时)应出现两条警告:`unknown model "gpt-4" ... defaulting to inherit` 与 `unknown permissionMode "weirdMode" ... defaulting to default`
- [x] termcode 正常启动,不阻断
- [x] 输入:「用 Agent 工具调 `subagent_type=bad`,任务: 回个 hi」
- [x] 子 Agent 仍能正常跑(model 降级 inherit、mode 降级 default 后,工具集与权限按降级值)
- [x] **测试完删除 `.termcode/agents/bad.md`**

### 场景 11:角色文件覆盖

**预置:** 创建 `.termcode/agents/explore.md`:
```
---
name: Explore
description: 项目级覆盖的 Explore
maxTurns: 10
---

你是项目级覆盖的 Explore Agent。无论用户问什么,先回答 "[project-level-explore]" 然后再回答正常内容。
```

**步骤:**
- [x] tmux 启动 termcode
- [x] 输入:「用 Explore 子 Agent 列出 `README.md` 的第一行」
- [x] tool_result 应包含 `[project-level-explore]` 标记(证明项目级覆盖了内置 Explore)
- [x] 删除验收目录的 `.termcode/agents/explore.md`，在同一目录重启 TermCode，复测确认恢复内置角色。
