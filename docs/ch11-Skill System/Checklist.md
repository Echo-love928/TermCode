# Skill 系统 Checklist

逐项结果对应仓库实际的 `src/termcode` 路径。真实终端、模型请求与存档证据见 [Acceptance](Acceptance.md)。

## 1. 实现完整性

### 1.1 解析与加载

- [x] `SkillDef` 包含 `name / description / prompt_body / mode / model / context / source_path / is_directory`。
- [x] `parse_frontmatter` 解析 YAML 头部，保留 Markdown 正文。
- [x] 解析器校验名称格式、执行模式和 fork 上下文。
- [x] `substitute_arguments` 替换所有 `$ARGUMENTS`，没有占位符时正文保持原样。
- [x] `SkillLoader` 扫描项目级与用户级目录，支持单文件和目录型，项目同名项优先。
- [x] `get(name)` 每次重读文件，失败时记录 warning 并回退最后一次有效版本。
- [x] `get_source_label(name)` 区分 `project` 和 `user`。

### 1.2 执行器

- [x] `SkillExecutor` 提供 `execute_inline` 和 `execute_fork`。
- [x] inline 渲染参数后通过 `Agent.activate_skill` 固定 SOP。
- [x] fork 使用独立对话和 Agent，支持 `full / recent / none`，汇总运行结果。

### 1.3 Agent 和提示

- [x] 会话状态持有活动 Skill 字典，Agent 提供激活、清空和设置目录接口。
- [x] Agent 每次模型调用前构建环境段。激活前只有目录，激活后注入 `## Active Skills` 和 SOP。

### 1.4 LoadSkill

- [x] `LoadSkill` 是只读工具，通过名称选择 Skill，串行更新活动状态。
- [x] 启动和会话切换时绑定当前加载器、Agent 和执行器。
- [x] inline 的工具结果只确认激活。fork 的工具结果回传子 Agent 的输出。
- [x] fork 子 Agent 从工具集中排除 `SYSTEM_TOOL_NAMES = frozenset({"LoadSkill"})`。

### 1.5 命令

- [x] 每个 Skill 可通过斜杠命令运行，帮助中带 `[skill]`。
- [x] 每个命令注册表独立维护 Skill 覆盖项，重载时删除旧项并恢复被覆盖的内置命令。
- [x] inline 命令先激活再发送用户触发消息，参数可进入 SOP。
- [x] fork 命令使用后台流任务，结果通过现有 assistant 消息协议回流到主会话。
- [x] `/skill list`、`/skill info <name>` 和 `/skill reload` 可用。
- [x] `/clear` 通过会话重置清空激活态。

### 1.6 远程安装

- [x] `parse_skill_url` 支持 skills.sh、GitHub tree 和 raw SKILL.md 链接。
- [x] GitHub Contents API 递归下载到用户目录中的暂存目录，验证后原子发布。
- [x] 单文件 1 MiB、总量 8 MiB、64 个文件、4 层深度限制均有测试。
- [x] 缺少或无法解析 `SKILL.md` 时拒绝安装并清理暂存目录。
- [x] `InstallSkill` 属于写入工具，使用现有权限流程。
- [x] 安装后刷新目录与斜杠命令。

## 2. 接入完整性

- [x] [CLI](../../src/termcode/cli.py) 构造加载器，传入 [TUI](../../src/termcode/tui/app.py)。
- [x] [Agent](../../src/termcode/agent/agent.py)、[执行器](../../src/termcode/skills/executor.py) 与 [LoadSkill](../../src/termcode/tool/load_skill.py) 的调用链连通。
- [x] `/clear` 走 [会话重置](../../src/termcode/tui/commands.py)，同时清除会话内的 Skill 状态。
- [x] [命令处理](../../src/termcode/command/skills.py) 调用加载器，再由现有 UI 协议分发给 inline 或 fork 执行器。
- [x] CLI 注册 `LoadSkill`、`InstallSkill` 和管理命令，支持会话恢复后重新绑定。

## 3. 静态检查与回归

- [x] `ruff check src/termcode tests` 通过。
- [x] `ruff format --check src/termcode tests` 和 `mypy src/termcode` 通过。
- [x] `pytest tests/test_skills.py -q` 全部通过。
- [x] `pytest tests/test_agent.py -q` 通过。
- [x] `SkillLoader`、`LoadSkill` 导入冒烟检查，输出 `SkillLoader LoadSkill read`。
- [x] 全量 pytest 复跑 538 项通过，最终 Skill 与权限专项 52 项通过。

## 4. 真实 tmux 端到端

- [x] 在隔离项目中创建 `test-skill/SKILL.md`，以正式 CLI 启动。
- [x] `/help` 显示 `/test-skill` 和 `/skill`。
- [x] `/test-skill` 将 SOP 注入发送给模型的环境提示。
- [x] 修改同一文件后，在同一个 tmux 会话再次调用，模型请求包含新正文和参数。
- [x] 自然语言触发 `LoadSkill`。实际配置中的模型调用工具并回复 SOP 核对码。
- [x] `/clear` 后立即提交新消息，环境中不再出现旧的 `## Active Skills`。
- [x] 无效的 `bad.md` 产生 warning，其余 Skill 仍可使用。
- [x] 默认权限模式下 `LoadSkill` 没有弹出确认。
- [x] fork 子 Agent 实际读文件并回传核对码，主对话不含子对话工具记录。
- [x] 真实 GitHub 安装通过授权并写入隔离用户目录，目录与帮助立即更新。

## 5. 文档与提交

- [x] [Spec](Spec.md) 和 [Plan](Plan.md) 记录与仓库适配的实现口径。
- [x] [Acceptance](Acceptance.md) 记录逐项测试和 tmux 证据。
- [x] 本次交付使用一条提交，标题为 `实现Skill System`。
