# ch11 Skill 系统验收记录

日期 2026-11-07。端到端测试在 Ubuntu 的真实 tmux 中运行 TermCode CLI。测试使用隔离的项目与用户目录、合成的 Skill 和样例文件。

全量 pytest 复跑 538 项通过。首轮有一项既有权限取消测试等待超时，复跑通过。最后补充的 YAML 键校验和 fork 暂存目录回退通过 Skill 与权限专项验证，共 52 项通过。较早的 Skill 与 Agent 专项共 62 项通过。ch10 斜杠命令的 tmux 回归覆盖 31 个证据点。导入冒烟检查输出 `SkillLoader LoadSkill read`。

| 场景 | 实际观察 | 证据 |
| --- | --- | --- |
| 启动和管理 | 无效的 `bad.md` 产生 warning，其余 Skill 正常加载。帮助显示 `/skill` 和带 `[skill]` 的 Skill 命令。列表显示项目来源，详情显示路径和目录标记。 | [启动](../evidence/tmux-ch11-github-start.txt)、[帮助](../evidence/tmux-ch11-github-help.txt)、[列表](../evidence/tmux-ch11-github-list.txt)、[详情](../evidence/tmux-ch11-github-info.txt) |
| 显式 inline 和热更新 | 第一次模型请求含 `Echo hello`。同一 tmux 会话修改文件后，请求含新核对码 `CH11_UPDATED_194` 和渲染后的参数“指定目标”。同名用户级 Skill 的内容没有注入。 | [首次执行](../evidence/tmux-ch11-github-inline.txt)、[热更新](../evidence/tmux-ch11-github-hot-reload.txt) |
| 显式 fork | 项目的 `/review` 覆盖内置命令，子请求使用指定模型和隔离上下文。子 Agent 实际调用 `read_file` 并读取 `CH11_READ_246`，主会话存档只增加一条 assistant 结果。 | [fork 输出](../evidence/tmux-ch11-github-fork.txt) |
| 意图触发与渐进式披露 | 本地 SSE 首次请求只有目录，`LoadSkill` 调用没有权限弹窗。第二次请求的环境提示包含 SOP，工具响应只确认激活，第三次请求包含实际文件读取结果。 | [自然语言触发](../evidence/tmux-ch11-github-intent.txt) |
| `/clear` 与重载 | 清空会话后的模型请求无 `## Active Skills`。`/skill reload` 添加 `/fresh` 并恢复移除覆盖后的内置 `/review`。 | [清空验证](../evidence/tmux-ch11-github-clear-confirmed.txt)、[重载后的帮助](../evidence/tmux-ch11-github-reload-help.txt) |
| 远程安装 | 选择“允许本次”后，从真实 GitHub Contents API 下载 `find-skills` 到隔离的用户目录。安装后的同一轮模型请求含新增目录项，`/help` 无需重启即可发现命令。 | [权限确认](../evidence/tmux-ch11-github-install-approval.txt)、[安装结果](../evidence/tmux-ch11-github-install.txt)、[安装后的帮助](../evidence/tmux-ch11-github-install-help.txt)、[GitHub Contents API](https://docs.github.com/en/rest/repos/contents#get-repository-content) |
| 配置中的模型 | 在另一场 tmux 会话中输入“请按已提供的验收测试流程执行，返回流程里指定的核对码”。实际观察到模型根据目录选择 `test-skill`、调用 `LoadSkill`，TermCode 确认激活，模型回复 `CH11_LIVE_802`。 | [模型调用和回复](../evidence/tmux-ch11-live-intent.txt) |
| 退出 | 两场 tmux 验收退出码均为 0，退出前后终端模式一致。 | [真实 GitHub 验收结果](../evidence/ch11-github-results.json)、[配置模型验收结果](../evidence/ch11-live-results.json) |

在仓库根目录的 WSL 环境复跑。

```sh
.venv-linux/bin/python -m pytest -q
PYTHONPATH=src .venv-linux/bin/python tests/tmux_skills_e2e.py
PYTHONPATH=src .venv-linux/bin/python tests/tmux_skills_e2e.py --github
PYTHONPATH=src .venv-linux/bin/python tests/tmux_skills_e2e.py --live
```

默认脚本使用本地 SSE 和下载测试响应。`--github` 访问公开仓库并使用本地 SSE。`--live` 使用项目配置中的首个模型，仅发送合成的验收请求和测试 Skill。
