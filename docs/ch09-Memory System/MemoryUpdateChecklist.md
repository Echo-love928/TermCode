# 身份记忆更新修复验收

日期：2026-11-06。

## 原因与修复范围

原有触发条件只有“记住”等关键词或每第五轮。“我一名28届的计算机毕业生……”这样的自然身份补充没有触发整理；定时整理又只截取第五轮，漏掉了前四轮。旧索引因此一直保留旧身份，且整理提示没有明确规定最新用户陈述优先、同主题更新原笔记。

本次只调整 Agent 的记忆触发与快照、记忆整理提示，以及相关回归和真实终端验收。保留原有后台异步方式和每五轮整理频率；自然身份陈述与纠正可提前触发。定时整理覆盖最近五轮。整理器按最新直接陈述合并已有笔记，标题与正文同步更新，不根据技术熟练度推断“资深”等身份。

## 检查项目

- [x] 先复现再修复：新增回归在旧实现中出现三项失败，分别为五轮窗口遗漏、中文身份补充未更新和英文身份补充未更新。
- [x] 同一笔记更新：中文与英文自然陈述不需要“记住”关键词；保留文件名与 `created`，推进 `updated`，替换旧身份并刷新索引。下一轮请求注入更新后的身份；“现在告诉我我是谁”本身不触发额外整理。
- [x] 定时窗口完整且不阻塞：第五轮发送最近五轮完整快照，第六轮显式请求仍发送独立快照；整理器被阻塞时对话仍能完成。
- [x] Windows 全量回归：`PYTHONUTF8=1 python -m pytest -q --timeout=45`，418 passed、2 skipped，92.63 秒。[测试报告](../evidence/memory-correction-all.xml)。未设置 UTF-8 时，现有恢复测试因默认 GBK 读取中文而失败，未为此修改恢复模块。
- [x] Linux 记忆回归：`pytest tests/test_memory.py tests/test_memory_agent.py`，7 passed，19.21 秒。[测试报告](../evidence/memory-correction-linux.xml)。
- [x] 真实 tmux 与模型：先确认 Python 工程师身份，再自然补充 28 届计算机背景和 AI Agent 学习目标；观察同一笔记和索引更新，重启新进程后正确引用新身份。两个进程均正常退出并还原终端。[实际结果](../evidence/memory-update-live-results.json)、[首轮笔记](../evidence/memory-update-first-note.md)、[更新后笔记](../evidence/memory-update-second-note.md)、[新进程回复](../evidence/tmux-memory-update-restart.txt)。新回复为“你是一名 28 届计算机毕业生，具备深入的 Python 专业知识，计划学习最新的 AI Agent 开发”。
- [x] lint 与格式检查完成：`ruff check src/termcode tests`、`ruff format --check src/termcode tests` 通过（116 个文件）；本次两个源码文件使用 `mypy --follow-imports=silent` 检查通过。
- [ ] 全仓类型检查通过。当前仍有 11 条现有错误，位于 conversation、session 和 tui/resume；不将其标记为通过。

## 证据与运行方式

真实模型验收脚本：`tests/tmux_memory_update_live_e2e.py`。脚本使用已有供应商配置，但工作目录、用户目录和记忆均隔离在 `.cache/tmux-e2e/`，不改动真实用户笔记，不上传密钥。需要 WSL / Linux、tmux 和可用 Anthropic 协议配置。

```sh
.venv-linux/bin/python tests/tmux_memory_update_live_e2e.py
```

原始模型和终端记录位于已忽略的 `docs/evidence/`，仅在本机保存。

## 已有身份笔记修正

本机原有身份笔记也已按用户确认的两段自述在本地更新：保留 Python 专业背景，补入 28 届计算机毕业生与 AI Agent 学习目标，删除未经确认的“资深”称谓，并同步索引。保留原创建时间，旧笔记与索引已备份在 `.cache/`。该修正没有调用外部模型。[本地修正结果](../evidence/memory-update-existing-profile.json)。
