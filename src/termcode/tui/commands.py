"""将命令 UI 协议映射到现有 App，集中处理错误与忙碌状态。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from rich.text import Text

from termcode.agent import NOTICE_CANCELLED, CompactEvent, CompactPhase
from termcode.command import Kind, parse
from termcode.compact import new_session_context
from termcode.conversation import Conversation
from termcode.hook import Event as HookEvent
from termcode.hook.rule import Rule
from termcode.permission import Mode
from termcode.session import Writer
from termcode.skills.parser import SkillDef
from termcode.tui.compact import format_compact_notice
from termcode.tui.view import error_block

if TYPE_CHECKING:
    from termcode.tui.app import TermCodeApp


class CommandUI:
    """使用适配器，避免协议方法与 App 现有的 mode、usage 字段同名。"""

    def __init__(self, app: TermCodeApp) -> None:
        self.app = app
        self.quit_task: asyncio.Task[None] | None = None
        self.resume_task: asyncio.Task[None] | None = None

    def println(self, msg: str) -> None:
        self.app._pending_println.append(msg)

    def error(self, msg: str) -> None:
        self.app._pending_println.append(f"ERROR\x00{msg}")

    def mode(self) -> Mode:
        return self.app.mode

    def set_mode(self, mode: Mode) -> None:
        self.app.mode = mode
        if self.app.is_running:
            self.app._refresh_statusbar()

    def inject_and_send(self, display_label: str, preset_prompt: str) -> None:
        self.app.conv.add_user(preset_prompt)
        self.app._begin_turn(display_label)

    def run_skill(self, skill: SkillDef, args: str) -> None:
        app = self.app
        app._ensure_agent()
        executor = app.skill_executor
        if executor is None:
            raise RuntimeError("Skill 执行器未就绪")
        trigger = f"/{skill.name}" + (f" {args}" if args else "")
        if skill.mode == "fork":
            app._begin_turn(trigger, fork=(skill, args))
            return
        executor.execute_inline(skill, args)
        self.inject_and_send(trigger, f"请执行已激活的 Skill {trigger}")

    def usage_in(self) -> int:
        return self.app.usage_in

    def usage_out(self) -> int:
        return self.app.usage_out

    def model_name(self) -> str:
        return self.app.provider.model if self.app.provider else ""

    def cwd(self) -> str:
        return self.app.workspace

    def tool_count(self) -> int:
        return self.app._tool_registry.count()

    def memory_files(self) -> list[str]:
        if self.app.mem_mgr is None:
            return []
        project, user = self.app.mem_mgr.list_files()
        return project + user

    def hook_sources(self) -> list[str]:
        engine = self.app.hook_engine
        return engine.sources if engine is not None else []

    def hook_rules(self) -> list[Rule]:
        engine = self.app.hook_engine
        return engine.rules if engine is not None else []

    def session_path(self) -> str:
        return self.app.writer.path if self.app.writer else ""

    def session_id(self) -> str:
        return self.app.runtime.session.session_id if self.app.writer else ""

    def idle(self) -> bool:
        from termcode.tui.app import SessionState

        return self.app.state == SessionState.IDLE

    def quit(self) -> None:
        self.quit_task = asyncio.create_task(self.app.action_quit())

    def force_compact(self) -> None:
        from termcode.tui.app import SessionState

        app = self.app
        if app.agent is None:
            self.error("agent 未就绪")
            return
        app._manual_compact = True
        app.state = SessionState.STREAMING
        app.turn_cancel = asyncio.Event()
        self.println("正在压缩上下文...")
        app._stream_task = asyncio.create_task(self._run_force_compact())

    async def _run_force_compact(self) -> None:
        app = self.app
        try:
            defs = (
                app._tool_registry.read_only_definitions()
                if app.mode == Mode.PLAN
                else app._tool_registry.definitions()
            )
            before, after = await app._ensure_agent().run_force_compact(
                app.conv, defs, app.mode
            )
            event = CompactEvent(CompactPhase.AFTER_AUTO, before, after)
            self.println(format_compact_notice(event))
        except asyncio.CancelledError:
            if not app._quitting:
                self.println(NOTICE_CANCELLED)
            raise
        except Exception as exc:
            event = CompactEvent(CompactPhase.AFTER_AUTO, err=exc)
            self.println(format_compact_notice(event))
        finally:
            app._manual_compact = False
            if not app._quitting:
                flush_output(app)
                app._return_to_idle()

    def open_resume_menu(self) -> None:
        from termcode.tui.app import SessionState
        from termcode.tui.resume import open_resume_menu

        # 启动异步目录查询前先占用状态，防止相邻提交并发修改会话。
        self.app.state = SessionState.RESUMING
        self.resume_task = asyncio.create_task(open_resume_menu(self.app))
        self.app._stream_task = self.resume_task

    async def clear_and_new_session(self) -> None:
        from textual.widgets import RichLog

        from termcode.tui.app import SessionState

        app = self.app
        if app.runtime.run_lock.locked():
            raise RuntimeError("请等待当前任务完成")
        context = new_session_context(app.workspace)
        writer = Writer(context.session_dir, self.model_name())
        conv = Conversation(writer.on_append, writer.on_replace)
        old_writer = app.writer
        app.state = SessionState.RESUMING
        try:
            await app.end_hook_session()
            await app._clear_tasks()
            if old_writer:
                old_writer.close()
            app.runtime.reset_for_new_session(context)
            app.writer, app.conv = writer, conv
            app.iter = app.usage_in = app.usage_out = 0
            app.cur_reply = ""
            app.cur_tools = []
            app._transcript.clear()
            if app.is_running:
                app.query_one("#log", RichLog).clear()
                app._refresh_statusbar()
            await app._open_hook_session(HookEvent.SESSION_START)
            self.println("已清空当前会话，开启新 session")
        finally:
            if app.writer is not writer:
                writer.close()
            app.state = SessionState.IDLE


def flush_output(app: TermCodeApp) -> None:
    if not app.is_running or not app.screen_stack[0].query("#log"):
        return
    pending, app._pending_println = app._pending_println, []
    for msg in pending:
        if msg.startswith("ERROR\x00"):
            app._write(error_block(app._clean(msg[6:]), 0))
        else:
            app._write(Text(app._clean(msg), style="dim"))


async def dispatch_slash(app: TermCodeApp, text: str) -> bool:
    name, is_slash = parse(text)
    if not is_slash:
        return False
    ui = app.command_ui
    app._pending_println.clear()
    args = ""
    if name:
        cmd = app.cmd_registry.lookup(name)
    else:
        cmd = None
        slash_text = text.strip()[1:]
        parts = (
            slash_text.split(maxsplit=1) if slash_text == slash_text.lstrip() else []
        )
        if len(parts) == 2:
            name, args = parts
            cmd = app.cmd_registry.lookup(name, base_only=name.lower() == "skill")
            if cmd is not None and cmd.argument_handler is None:
                cmd = None
    if cmd is None:
        ui.println("未知命令：输入 /help 查看可用命令")
    elif cmd.kind != Kind.LOCAL and (not ui.idle() or app._quitting):
        ui.error("请等待当前任务完成")
    else:
        try:
            if cmd.argument_handler is not None:
                await cmd.argument_handler(ui, args)
            else:
                await cmd.handler(ui)
            if ui.resume_task is not None:
                await ui.resume_task
                ui.resume_task = None
            if ui.quit_task is not None:
                await ui.quit_task
                ui.quit_task = None
        except Exception as exc:
            ui.error(str(exc))
    flush_output(app)
    return True
