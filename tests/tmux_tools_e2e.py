"""ch03 的真实 tmux 验收；默认本地 SSE，--live 使用现有模型配置。"""

import json
import sys
import time
from dataclasses import asdict

from test_tool_protocols import call
from tmux_e2e import EVIDENCE, ROOT, TmuxRun

from termcode.config import load


class ToolRun(TmuxRun):
    def __init__(self, live=False):
        super().__init__()
        self.live = live
        self.secrets = []
        self.results["backend"] = (
            "configured live models" if live else "local SSE fixture"
        )

    def request(self, session, prompt, marker, responses=None, running=False):
        if responses:
            self.server.responses = responses
        self.send(session, prompt)
        self.key(session, "Enter")
        if running:
            self.wait(lambda: "Running…" in self.capture(session), "工具运行指示")
            self.capture(session, f"ch03-{session}-running")
        self.wait(
            lambda: (
                marker in self.capture(session)
                and "完成 ·" in self.capture(session)
                and "Send a message..." in self.capture(session)
                and "Imagining" not in self.capture(session)
                and "Running…" not in self.capture(session)
            ),
            marker,
            seconds=150,
        )
        # 输入解锁与动态区移除发生在同一事件循环末尾。
        time.sleep(0.2)

    def capture(self, session, name=None):
        text = super().capture(session, name)
        assert all(secret not in text for secret in self.secrets if secret)
        return text

    def run(self):
        entries = None
        if self.live:
            cfg = load(ROOT / ".termcode/config.yaml")
            entries = [asdict(p) for p in cfg.providers]
            self.secrets = [p.api_key for p in cfg.providers]
        for protocol in ("openai", "anthropic"):
            if self.live:
                entry = next((e for e in entries if e["protocol"] == protocol), None)
                if entry is None:
                    self.results[protocol] = "未配置，未验收"
                    continue
            else:
                entry = self.entry(protocol, f"tools-{protocol}")
            session = f"tools-{protocol}"
            work = self.start(session, [entry])
            target = str(work / "nested/probe.txt")
            spec = str(ROOT / "docs/ch03-ToolSystem/Spec.md")
            if self.live:
                self.request(
                    session,
                    f"请实际调用 read_file 读取 {spec}，用一句中文总结，"
                    "最终答复以验收读完成结尾。",
                    "验收读完成",
                )
                self.capture(session, f"ch03-live-{protocol}-read")
                self.request(
                    session,
                    "请在同一次回复中依次请求五个工具（不要分轮）："
                    f"write_file 创建 {target}，"
                    "内容为 alpha；edit_file 将 alpha 唯一替换为 beta；"
                    f"bash 执行 cat {target}；glob 在 {work} 查找 **/*.txt；"
                    f"grep 在 {target} 搜 beta。根据工具结果回复，末尾写验收写改完成。",
                    "验收写改完成",
                )
                self.capture(session, f"ch03-live-{protocol}-batch")
                assert (work / "nested/probe.txt").read_text() == "beta"
                self.request(
                    session,
                    f"请在同一次回复中实际调用三个工具：read_file 读 {target}.missing；"
                    f"edit_file 将 {target} 中不存在的 absent 替换为 x；"
                    "bash 执行 exit 7。解释失败原因，答复末尾写验收错误完成。",
                    "验收错误完成",
                )
                self.capture(session, f"ch03-live-{protocol}-errors")
                self.request(
                    session,
                    "你能做什么？简短回答，末尾写验收继续完成。",
                    "验收继续完成",
                )
            else:
                self.request(
                    session,
                    f"读取 {spec} 并总结。",
                    "读取总结完成",
                    [
                        {
                            "text": "先读取规格。",
                            "calls": [call("read_file", path=spec)],
                        },
                        {"text": "读取总结完成：六个工具、双协议与单轮续答。"},
                    ],
                )
                self.capture(session, f"ch03-{protocol}-read")
                self.request(
                    session,
                    "新建文件、修改、执行查看并查找搜索。",
                    "写改执行完成",
                    [
                        {
                            "calls": [
                                call("write_file", path=target, content="alpha"),
                                call(
                                    "edit_file",
                                    id="c2",
                                    path=target,
                                    old_string="alpha",
                                    new_string="beta",
                                ),
                                call("bash", id="c3", command=f"cat {target}"),
                                call(
                                    "glob", id="c4", path=str(work), pattern="**/*.txt"
                                ),
                                call("grep", id="c5", path=target, pattern="beta"),
                            ]
                        },
                        {"text": "写改执行完成：文件内容为 beta。"},
                    ],
                )
                assert (work / "nested/probe.txt").read_text() == "beta"
                self.capture(session, f"ch03-{protocol}-batch")
                self.request(
                    session,
                    "分别触发读文件、修改和命令错误。",
                    "错误反馈完成",
                    [
                        {
                            "calls": [
                                call("read_file", path=target + ".missing"),
                                call(
                                    "edit_file",
                                    id="c2",
                                    path=target,
                                    old_string="absent",
                                    new_string="x",
                                ),
                                call("bash", id="c3", command="exit 7"),
                            ]
                        },
                        {"text": "错误反馈完成：文件不存在、匹配 0 处、退出码 7。"},
                    ],
                )
                self.capture(session, f"ch03-{protocol}-errors")
                self.request(
                    session,
                    "运行稍慢的命令，观察进行中界面。",
                    "慢命令完成",
                    [
                        {"calls": [call("bash", command="sleep 2; echo slow-ok")]},
                        {"text": "慢命令完成。"},
                    ],
                    running=True,
                )
                self.request(
                    session, "继续对话。", "继续完成", [{"text": "继续完成。"}]
                )
            self.key(session, "PPage")
            self.capture(
                session, f"ch03-{'live-' if self.live else ''}{protocol}-history"
            )
            self.key(session, "NPage")
            self.finish(session, work)
            final = self.capture(
                session, f"ch03-{'live-' if self.live else ''}{protocol}-exit"
            )
            assert "● read_file(" in final and "● bash(" in final
            for name in ("write_file", "edit_file", "glob", "grep"):
                assert f"● {name}(" in final, f"未找到工具行 {name}"
            assert final.index("● read_file(") < final.index("● write_file(")
            assert "匹配到 0 处" in final and "exit_code: 7" in final
            if not self.live:
                assert "[truncated]" in final
        if not self.live:
            self.results["requests"] = [
                {
                    "path": r["path"],
                    "tool_count": len(r["body"].get("tools", [])),
                    "roles": [m["role"] for m in r["body"]["messages"]],
                }
                for r in self.server.requests
            ]
        name = "ch03-live-results.json" if self.live else "ch03-tmux-results.json"
        (EVIDENCE / name).write_text(
            json.dumps(self.results, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    run = ToolRun(live="--live" in sys.argv)
    try:
        run.run()
        print(json.dumps(run.results, ensure_ascii=False, indent=2))
    finally:
        run.close()
