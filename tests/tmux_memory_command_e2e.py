"""在真实 tmux 中验证 /memory，只使用隔离的人工记忆和本地 SSE。"""

import json

from tmux_e2e import EVIDENCE, ROOT, TmuxRun

from termcode.memory import Store


class MemoryCommandRun(TmuxRun):
    def run(self):
        session = "memory-command"
        work = ROOT / ".cache/tmux-e2e" / self.socket / session
        isolated_home = work / "home"
        isolated_home.mkdir(parents=True)
        self.tmux("new-session", "-d", "-s", "bootstrap", "sleep 3600")
        self.tmux("set-environment", "-g", "HOME", str(isolated_home))
        self.start(session, [self.entry("openai", "local")])

        def command(marker, capture_name):
            requests = len(self.server.requests)
            self.send(session, "/memory")
            self.key(session, "Enter")
            self.wait(lambda: marker in self.capture(session), "显示记忆")
            self.capture(session, capture_name)
            assert len(self.server.requests) == requests

        command("暂无已保存的记忆", "memory-command-empty")
        assert not self.server.requests
        user_store = Store(str(isolated_home / ".termcode/memory"))
        project_store = Store(str(work / ".termcode/memory"))
        user_store.apply(
            [
                dict(
                    action="create",
                    type="user_preference",
                    slug="background",
                    title="用户画像",
                    content="测试用户：28届计算机毕业生，学习 AI Agent 开发。",
                )
            ]
        )
        project_store.apply(
            [
                dict(
                    action="create",
                    type="project_knowledge",
                    slug="stack",
                    title="项目技术栈",
                    content="项目使用 Python 和 Textual。",
                )
            ]
        )
        before = {
            path: path.read_bytes()
            for store in (user_store, project_store)
            for path in store.directory.glob("*.md")
        }
        command("项目使用 Python 和 Textual", "memory-command-populated")
        text = self.capture(session)
        assert "Memories (2 entries):" in text
        assert "[user]" in text and "[project]" in text
        assert "28届" in text and "AI Agent" in text
        assert all(path.read_bytes() == data for path, data in before.items())

        user_store.apply(
            [
                dict(
                    action="update",
                    filename="user_preference_background.md",
                    title="最新用户画像",
                    content="最新学习目标：多智能体开发。",
                )
            ]
        )
        command("最新学习目标", "memory-command-refreshed")
        assert not self.server.requests
        self.tmux("resize-window", "-t", session, "-x", "38", "-y", "20")
        self.wait(lambda: "Send a message" in self.capture(session), "窄屏输入框")
        self.capture(session, "memory-command-narrow")

        self.server.responses = [{"text": "VIEW_CHAT_OK"}]
        self.turn(session, "你好，请只回复 VIEW_CHAT_OK。")
        self.wait(lambda: "VIEW_CHAT_OK" in self.capture(session), "正常聊天仍可使用")
        assert len(self.server.requests) == 1
        users = [
            msg["content"]
            for msg in self.server.requests[0]["body"]["messages"]
            if msg["role"] == "user"
        ]
        assert users == ["你好，请只回复 VIEW_CHAT_OK。"]
        self.capture(session, "memory-command-chat")
        self.finish(session, work)
        archive = next((work / ".termcode/sessions").glob("*/conversation.jsonl"))
        rows = [json.loads(line) for line in archive.read_text().splitlines()]
        assert all(row.get("content") != "/memory" for row in rows)
        result = self.results | {
            "empty_state": True,
            "both_levels_and_full_body": True,
            "live_file_refresh": True,
            "read_only": True,
            "memory_command_model_requests": 0,
            "ordinary_chat_requests": 1,
            "command_not_archived": True,
            "narrow_width": 38,
            "real_user_data_used": False,
        }
        (EVIDENCE / "memory-command-tmux-results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    run = MemoryCommandRun()
    try:
        run.run()
    finally:
        run.close()
