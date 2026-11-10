"""从当前项目配置启动 TermCode。"""

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

from termcode.config import Config as ProviderConfig
from termcode.config import ConfigError, load


def main() -> None:
    if sys.argv[1:2] == ["--team-member"]:
        from termcode.team.member import parse_args, run_team_member

        args = parse_args(sys.argv[2:])
        try:
            config = load(args.project_root / ".termcode/config.yaml")
            asyncio.run(run_team_member(args, config))
        except (ConfigError, OSError, ValueError, RuntimeError) as exc:
            print(f"TermCode 队员：{exc}", file=sys.stderr)
            raise SystemExit(1) from None
        except KeyboardInterrupt:
            pass
        return
    if sys.argv[1:] in (["--help"], ["-h"]):
        print(
            "用法: termcode [--help | --version]\n"
            "从当前目录的 .termcode/config.yaml 启动对话。\n"
            "队员窗格入口：termcode --team-member --help"
        )
        return
    if sys.argv[1:] == ["--version"]:
        from termcode import __version__

        print(f"TermCode {__version__}")
        return
    try:
        config = load(".termcode/config.yaml")
    except ConfigError as exc:
        print(f"TermCode: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        pass


async def _run(config: ProviderConfig) -> None:
    from termcode import __version__, coordinator, hook, team, worktree
    from termcode.agent import SessionRuntime
    from termcode.agent.agent_tool import AgentTool
    from termcode.compact import new_session_context
    from termcode.config import effective_context_window
    from termcode.instructions import Loader
    from termcode.mcp import load_config, new_manager
    from termcode.memory import Manager
    from termcode.permission import new_engine
    from termcode.session import Writer, clean_expired
    from termcode.skills.loader import SkillLoader
    from termcode.subagent import load_catalog
    from termcode.task import Manager as TaskManager
    from termcode.task import SendMessageTool, TaskGetTool, TaskListTool, TaskStopTool
    from termcode.team.feature import fork_teammate_enabled
    from termcode.team.tools import lifecycle_tools
    from termcode.tool import new_default_registry
    from termcode.tui.app import TermCodeApp
    from termcode.tui.keys import configure_terminal_keys

    configure_terminal_keys()
    root = str(Path.cwd())
    instruction_text = Loader(root).load()
    mem_mgr = Manager(
        str(Path(root) / ".termcode/memory"), str(Path.home() / ".termcode/memory")
    )
    memory_text = mem_mgr.load_index()
    runtime = SessionRuntime(
        session=new_session_context(root),
        context_window=effective_context_window(config.providers[0]),
    )
    registry = new_default_registry()
    catalog = load_catalog(root)
    try:
        worktree_mgr = worktree.Manager(root)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Worktree 管理器降级：{exc}", file=sys.stderr)
        worktree_mgr = None
    task_mgr = TaskManager()
    try:
        team_mgr = team.Manager(
            Path.home(),
            root,
            worktree_mgr,
            task_mgr,
            team.AgentNameRegistry(),
            fork_enabled=fork_teammate_enabled(config),
        )
    except OSError as exc:
        print(f"Agent Teams 不可用：{exc}", file=sys.stderr)
        team_mgr = None
    if team_mgr is not None:
        await team_mgr.recover()
        for team_tool in lifecycle_tools(team_mgr):
            registry.register(team_tool)
    for subagent_tool in (
        AgentTool(
            catalog,
            task_mgr,
            bg_enabled=config.enable_subagent_background,
            providers=tuple(config.providers),
            worktree_mgr=worktree_mgr,
            team_hook=team_mgr,
        ),
        TaskListTool(task_mgr),
        TaskGetTool(task_mgr),
        TaskStopTool(task_mgr),
        SendMessageTool(task_mgr),
    ):
        registry.register(subagent_tool)
    skill_loader = SkillLoader(root)
    skill_loader.load_all()
    try:
        writer = Writer(runtime.session.session_dir)
    except OSError:
        import logging

        logging.getLogger(__name__).exception("无法创建会话存档，对话继续运行")
        writer = None
    cleanup_task = asyncio.create_task(
        clean_expired(
            str(Path(root) / ".termcode/sessions"),
            protected=team_mgr.protected_sessions() if team_mgr else frozenset(),
        )
    )
    manager = await new_manager(load_config(root), __version__)
    sweep_task = (
        asyncio.create_task(
            worktree_mgr.sweep_stale(datetime.now() - timedelta(hours=24))
        )
        if worktree_mgr is not None
        else None
    )
    try:
        for tool in manager.tools():
            registry.register(tool)
        engine, err = new_engine(root)
        engine.allow_temp_files = True
        engine.mcp_tools = frozenset(tool.name() for tool in manager.tools())
        if err is not None:
            print("权限引擎降级：无法解析项目根目录", file=sys.stderr)
        hook_engine = hook.load(root)
        app = TermCodeApp(
            config.providers,
            registry=registry,
            engine=engine,
            runtime=runtime,
            writer=writer,
            mem_mgr=mem_mgr,
            instruction_text=instruction_text,
            memory_text=memory_text,
            cancel_background=manager.cancel_pending,
            skill_loader=skill_loader,
            hook_engine=hook_engine if hook_engine.rules else None,
            task_mgr=task_mgr,
            subagent_catalog=catalog,
            enable_subagent_background=config.enable_subagent_background,
            worktree_mgr=worktree_mgr,
            team_mgr=team_mgr,
            coordinator_mode=coordinator.is_enabled(config),
        )
        try:
            await app.run_async(inline=True)
        except KeyboardInterrupt:
            pass
        except Exception:
            print("TermCode: 终端界面运行失败，请检查终端环境。", file=sys.stderr)
            raise SystemExit(1) from None
        finally:
            try:
                await app.end_hook_session()
            finally:
                await hook_engine.aclose()
                app.print_transcript()
    finally:
        if sweep_task is not None:
            sweep_task.cancel()
            await asyncio.gather(sweep_task, return_exceptions=True)
        await task_mgr.clear()
        await mem_mgr.close()
        if writer:
            writer.close()
        await cleanup_task
        await manager.close()
