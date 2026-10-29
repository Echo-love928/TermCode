# ch04 Agent Loop 验收记录

验收日期：2026-10-29（Asia/Tokyo）。依据本目录 Spec、Plan、Tasks、Checklist。

## 实现结果

T1–T7 已完成：多轮 ReAct、25 轮上限、连续 3 轮全未知工具停止、流错误收尾、
及时取消、历史配对、只读分批并发、Token 累计、迭代进度、`/plan` 和 `/do`。
两协议均通过原有官方 SDK 请求；不增加依赖和配置项。

保留仓库原有接口布局：`Registry` 在 `tool/__init__.py`，`glob` / `grep` 的实现文件
为 `glob_tool.py` / `grep_tool.py`；工具参数继续使用原始 JSON 字符串。
`Agent.run` 的 `mode` / `cancel` 提供默认值以兼容既有调用方，TUI 显式传入。

取消会同时中断模型流等待和工具等待，回收监听任务；已完成结果保留，未完成和未启动
的调用补结构化取消结果，之后补 assistant 收尾。正常结果始终按模型调用顺序回灌。
计划模式只注入 read_file/glob/grep，同时在执行端拒绝非只读调用。

## 已执行验证

| 环境或检查 | 实际结果 |
| --- | --- |
| Windows / Python 3.14.3 全量 pytest | 106 passed，29.37s |
| Ubuntu WSL / Python 3.12.16 全量 pytest | 106 passed，74.94s |
| Agent 专项 | 18 passed；并发测试参数化重复 5 次，取消测试检查 asyncio 任务集合 |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 40 files already formatted |
| `mypy src/termcode` | Success: no issues found in 25 source files |
| 本地 SSE + 真 tmux + CLI + 官方 SDK | OpenAI、Anthropic 两种协议场景均通过，退出码 0，stty 前后一致 |
| 外部真实模型 | 两协议多轮、Plan/Do、能力答复及 Esc/Ctrl+C 取消恢复均通过；四个会话正常退出 |

Windows 沙箱内的 asyncio 初始化挂起；切到获准的原生进程后全量通过。
本次挂起的测试进程已按 PID 清理。WSL 测试使用独立 `.cache` 工作目录，不覆盖真实配置。

## tmux 实际观察

执行 `.venv-linux/bin/python tests/tmux_loop_e2e.py`，由 tmux 启动 `python -m termcode`，
输入自然语言“读 source.txt，根据内容新建 summary.txt，写一句话摘要”。
模型响应来自本地可控 SSE 服务，文件读写、shell、CLI、SDK、界面和键盘操作均真实执行。
这能确定性验证完整调用链，不能证明外部模型会按预期自主选择工具。

- 首轮并发读取两个文件，第二轮写入摘要，第三轮生成最终答复，无需中途输入。
  summary.txt 的实际内容为“多轮工具循环完成任务。”，恰好三次模型请求。
- OpenAI 第 2 / 3 轮状态栏从 `↑12 ↓10 tok` 增至 `↑24 ↓20 tok`；
  Anthropic 从 `↑1 ↓10 tok` 增至 `↑2 ↓20 tok`。动态区显示对应轮次。
- 各协议分别运行三次慢 shell，使用 Esc、Ctrl+C、Esc 取消；每次均回空闲，
  在同一进程内继续对话。空闲 Ctrl+C 正常退出。
- 本地服务直接断开连接，界面显示“网络连接失败”，服务恢复后原会话继续正常回复。
- `/plan` 后两轮请求仅携带 read_file/glob/grep，planned.txt 不存在；
  `/do` 后立即调用 write_file，磁盘内容为“计划已执行”。
- 退出后的完整 transcript 中 preamble、两条读取记录、摘要、写入记录和最终答复按序；
  大文件结果有 `[truncated]`。抓屏断言 TEST_SECRET 和思考内容均未出现。

证据：[双协议结果及请求角色序列](../evidence/ch04-tmux-results.json)、
[OpenAI 完整终端记录](../evidence/tmux-ch04-openai-exit.txt)、
[Anthropic 完整终端记录](../evidence/tmux-ch04-anthropic-exit.txt)、
[第二轮与用量](../evidence/tmux-ch04-openai-iteration2.txt)、
[第三轮与用量](../evidence/tmux-ch04-openai-iteration3.txt)、
[取消后恢复](../evidence/tmux-ch04-openai-cancel-1.txt)、
[连接错误](../evidence/tmux-ch04-anthropic-error.txt)、
[计划模式](../evidence/tmux-ch04-openai-plan.txt)、
[执行计划](../evidence/tmux-ch04-openai-do.txt)。
原始证据受 `.gitignore` 忽略，以上链接供本机查看。

## 取消资源复测

在每个真实 tmux Python 进程中，分别于多轮完成后及三次取消并继续对话后读取 `/proc`。

| 协议 | RSS KiB（基线 → 三次取消后） | 线程数 | 文件描述符 | 残留工具子进程 |
| --- | --- | --- | --- | --- |
| openai | 75120 → 75796 → 76044 → 76040 | 全部 4 | 全部 6 | 全部 0 |
| anthropic | 100248 → 101468 → 100904 → 101496 | 全部 4 | 全部 6 | 全部 0 |

第一次取消后的 RSS 最大波动分别为 248 KiB 和 592 KiB，未观察到持续资源增长。
这是三次循环的短期观测，不代表长期内存分析；取消单测另核对 asyncio 任务集合恢复至基线。
既有 `tests/tmux_e2e.py` 的五个输入、选择供应商、错误和取消恢复会话也已全部复测通过，
退出码均为 0、终端模式恢复，结果见 [基础 tmux 回归](../evidence/tmux-results.json)。

## Checklist 逐项结果

序号按 Checklist 中复选项从上到下排列。下表注明本地确定性服务与真实外部模型的实际验证范围。

| 序号 | 项目 | 结果与证据 |
| --- | --- | --- |
| 1 | 多轮自动连环 | 通过：本地与真实两协议 tmux 均读取 → 写入 → 最终答复；真实摘要文件内容核对正确。 |
| 2 | 自然完成 | 通过：`test_agent_multiround_read_write_history_and_final` 无工具即停；不发第四次请求。 |
| 3 | 迭代上限 | 通过：`test_iteration_limit` 恰好 25 次请求，NOTICE_MAX_ITER、done、assistant 收尾。 |
| 4 | 连续未知工具 | 通过：`test_unknown_limit_and_reset` 验证 3 轮停止与混入已知工具重置。 |
| 5 | 流出错恢复 | 本地通过：双协议 tmux 断连、错误块与继续对话；三类错误单测验证历史收尾。 |
| 6 | 事件流完备 | 通过：18 项 Agent 测试覆盖 text/tool/usage/iter/notice/done/err；TUI 仅消费事件。 |
| 7 | 流式双路收集 | 通过：`test_protocol_fragmented_batch_and_result_feedback` 交错名称/JSON 分片、三轮回灌；tmux 工具参数匹配。 |
| 8 | 保序分批并发 | 通过：`test_concurrent_batch` 重复 5 次，峰值 2、写入在读取之后、END 及结果原序。 |
| 9 | 取消历史一致 | 通过：单测核对调用 ID 配对及 assistant 尾部；本地和两个真实服务均取消后继续成功，无 400。 |
| 10 | Esc/Ctrl+C | 通过：本地与真实两协议均验证 Esc、Ctrl+C 取消后继续；空闲 Ctrl+C 退出码 0。 |
| 11 | 用量展示 | 本地通过：两协议第 2/3 轮抓屏记录数值递增；Pilot 验证跨请求累计。 |
| 12 | 进度展示 | 本地通过：iteration2/iteration3 抓屏分别显示“第 2 轮”“第 3 轮”。 |
| 13 | Plan Mode | 通过：本地请求体验证只读筛选；两个真实模型 /plan 调查后文件不存在，/do 实际写入。 |
| 14 | 外部跨协议一致 | 通过：deepseek-flash 与 glm-5.3-flash 均实际多轮读写、累计用量、Plan/Do、Esc/Ctrl+C 取消后继续，四个会话退出码 0。 |
| 15 | 历史携带 | 通过：Agent 和双协议测试核对 ID、角色及第三轮携带增长历史；JSON 证据记录角色。 |
| 16 | 界面不阻塞 | 本地通过：tmux 慢 shell 计时、取消响应；Pilot 工具等待时翻页、缩放。 |
| 17 | scrollback 顺序 | 本地通过：两条并发 read_file 按调用序提交，写入及最终答复顺序由完整终端记录断言。 |
| 18 | 结果体量 | 通过：工具测试覆盖 2100 行、1MB 单行、1MB shell 输出和超量匹配；tmux 多轮大文件显示截断。 |
| 19 | 取消无泄漏 | 本地通过：三次取消后线程/文件描述符不变、无子进程；RSS 波动小于 1MiB，详见资源复测。单测验证 asyncio 任务无残留。 |
| 20 | 系统提示与能力答复 | 通过：两个真实模型均明确回答可跨多轮连续使用工具，并按结果调整操作直到任务完成，见 live capabilities 记录。 |
| 21 | 启动 | 本地通过：两协议 python -m termcode 在真实 tmux 中进入 TUI。 |
| 22 | Ruff lint | 通过：All checks passed。 |
| 23 | Ruff format | 通过：40 files already formatted。 |
| 24 | Pytest | 通过：Windows/Linux 各 106 passed。 |
| 25 | Mypy | 通过：25 source files 无问题。 |
| 26 | 密钥不回显 | 通过：本地抓屏及 Pilot 检查测试密钥；真实模型每次抓屏均检索全部配置密钥，无明文出现。 |
| 27 | 场景 1 真实兼容端点 | 通过：deepseek-flash 实际 read_file → write_file → 最终答复；文件内容核对正确，用量显示 ↑3.5k ↓242 tok，/exit 退出码 0。 |
| 28 | 场景 2 用户取消 | 通过：本地三次取消；两个真实服务各 Esc/Ctrl+C 后原会话继续成功，并核对无残留工具子进程。 |
| 29 | 场景 3 流出错恢复 | 本地通过：直接断开本地 SSE 连接，错误后恢复同一会话；未覆盖修改 base_url 配置再加载。 |
| 30 | 场景 4 Plan/Do | 通过：本地与两个真实模型均计划时只调查且无文件，执行时 write_file 落盘，摘要内容正确。 |
| 31 | 场景 5 真实 Anthropic | 通过：glm-5.3-flash 实际读后写摘要，文件 108 字节；用量 ↑1.1k ↓304 tok；Plan/Do 与能力答复通过，/exit 退出码 0。 |
| 32 | 场景 6 迭代上限 | 通过：确定性单测验证文档允许的主要验收方式，未临时修改生产常量。 |
| 33 | 场景 7 连续未知工具 | 通过：确定性单测验证停止与计数重置，未修改生产提示词。 |

## 真实模型 tmux 验收

用户明确授权后，执行 `.venv-linux/bin/python tests/tmux_loop_e2e.py --live`，
由真实 tmux 输入自然语言，使用当前配置的 deepseek / zhipu。
只读取和创建本次独立 `.cache` 目录内的 source.txt、summary.txt、planned.txt，未覆盖真实配置。

| 协议 / 模型 | 多轮读取 → 写入 | /plan 只调查 | /do 写入计划文件 | 能力答复 | 退出与终端恢复 |
| --- | --- | --- | --- | --- | --- |
| OpenAI / deepseek-flash | 通过，摘要 117 字节 | 通过，planned.txt 尚不存在 | 通过，文件落盘 | 明确说明多轮推进 | 退出码 0，stty 一致 |
| Anthropic / glm-5.3-flash | 通过，摘要 108 字节 | 通过，planned.txt 尚不存在 | 通过，文件落盘 | 明确说明自主多轮工具调用 | 退出码 0，stty 一致 |

实际摘要均围绕“持续跨多轮调用工具，并根据每次结果调整后续操作，直到任务完成”。
计划阶段两模型均调用 read_file 调查；/do 后均实际调用 write_file，文件内容与方案一致。
状态栏累计用量继续增长：完成能力答复时，deepseek 为 ↑15.0k ↓1.3k tok，
zhipu 为 ↑4.7k ↓1.4k tok。每次抓屏均检查全部已配置密钥未出现。

证据：[真实模型结果](../evidence/ch04-live-results.json)、
[OpenAI 完整记录](../evidence/tmux-ch04-live-openai-exit.txt)、
[Anthropic 完整记录](../evidence/tmux-ch04-live-anthropic-exit.txt)、
[OpenAI 计划](../evidence/tmux-ch04-live-openai-plan.txt)、
[Anthropic 计划](../evidence/tmux-ch04-live-anthropic-plan.txt)、
[OpenAI 能力答复](../evidence/tmux-ch04-live-openai-capabilities.txt)、
[Anthropic 能力答复](../evidence/tmux-ch04-live-anthropic-capabilities.txt)。

补充执行 `tests/tmux_loop_e2e.py --live-cancel-only`：两协议分别实际调用 bash
执行 `sleep 20; echo cancel-probe`，在 Running 状态按 Esc，再次执行时按 Ctrl+C。
每次均出现“已取消”并返回空闲；随后同一会话发出新请求，真实模型正常回答，未报 HTTP 400。
继续回复后 `/proc` 检查无残留工具子进程；最终空闲 Ctrl+C 退出码 0，stty 前后一致。

证据：[真实取消结果](../evidence/ch04-live-cancel-results.json)、
[OpenAI 取消完整记录](../evidence/tmux-ch04-live-openai-cancel-exit.txt)、
[Anthropic 取消完整记录](../evidence/tmux-ch04-live-anthropic-cancel-exit.txt)、
[OpenAI 取消后继续](../evidence/tmux-ch04-live-openai-resume-1.txt)、
[Anthropic 取消后继续](../evidence/tmux-ch04-live-anthropic-resume-1.txt)。

两次真实验收合计四个会话均成功。全部 33 个 Checklist 项已有实际测试或终端观察证据；
连接错误与迭代/未知工具上限仍按文档采用本地故障注入及确定性单测验证。
更新后的 `--live` 脚本包含上述取消场景；也可用 `--live-cancel-only` 单独复现取消。

此前自动审批曾因缺少目的地授权拒绝运行；用户随后明确回复“授权”，本次外部请求均在授权后执行。

## 复现

```sh
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy src/termcode
# Linux / WSL，已安装 tmux
python tests/tmux_loop_e2e.py
# 获准发送至当前配置的外部模型后
python tests/tmux_loop_e2e.py --live
```
