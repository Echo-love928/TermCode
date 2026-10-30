"""流事件消费与计时；网络等待始终让出事件循环。"""

from __future__ import annotations

import asyncio
import time
from contextlib import aclosing
from typing import TYPE_CHECKING

from rich.text import Text

from termcode import __version__
from termcode.agent import NOTICE_CANCELLED, Agent, ApprovalRequest, Phase
from termcode.permission import Outcome
from termcode.tui.view import render_markdown, tool_line, tool_result_summary

if TYPE_CHECKING:
    from termcode.tui.app import TermCodeApp


async def consume_stream(app: TermCodeApp) -> None:
    if app.provider is None:
        app._finish_with_error(RuntimeError("尚未选择供应商"))
        return
    try:
        async with aclosing(
            Agent(app.provider, app._tool_registry, __version__, app.engine).run(
                app.conv, app.mode, app.turn_cancel
            )
        ) as stream:
            async for event in stream:
                if isinstance(event, ApprovalRequest):
                    from termcode.tui.app import SessionState

                    app.pending = event
                    app.approve_cursor = 0
                    app.state = SessionState.APPROVING
                    app.query_one("#input").blur()
                    app._refresh_streaming_view()
                    continue
                if event.err is not None:
                    app._finish_with_error(event.err)
                    return
                if event.tool is not None:
                    tool = event.tool
                    if tool.phase == Phase.START:
                        if app.cur_reply:
                            app._write(
                                render_markdown(
                                    app._clean(app.cur_reply),
                                    time.monotonic() - app.turn_start,
                                )
                            )
                            app.cur_reply = ""
                        app.cur_tools.append(tool)
                    else:
                        started = app.cur_tools.pop(0)
                        app._write(
                            tool_line(
                                app._clean(started.name), app._clean(started.args)
                            )
                        )
                        app._write(
                            tool_result_summary(app._clean(tool.result), tool.is_error)
                        )
                if event.usage is not None:
                    app.usage_in += event.usage.input
                    app.usage_out += event.usage.output
                    app._refresh_statusbar()
                if event.notice:
                    if app.cur_reply:
                        app._write(
                            render_markdown(
                                app._clean(app.cur_reply),
                                time.monotonic() - app.turn_start,
                            )
                        )
                        app.cur_reply = ""
                    app._write(Text(app._clean(event.notice), style="dim"))
                if event.iter:
                    app.iter = event.iter
                if event.text:
                    app.cur_reply += event.text
                if event.done:
                    if app.cur_reply:
                        app._finish_with_assistant(app.cur_reply)
                    else:
                        app._return_to_idle()
                    return
                app._refresh_streaming_view()
        if app.turn_cancel is not None and app.turn_cancel.is_set():
            if app.cur_reply:
                app._write(Text("● " + app._clean(app.cur_reply)))
            app._write(Text(NOTICE_CANCELLED, style="dim"))
            app._return_to_idle()
        else:
            app._finish_with_error(RuntimeError("响应流未正常结束，请重新发送问题"))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        app._finish_with_error(exc)


def tick(app: TermCodeApp) -> None:
    app._refresh_streaming_view()


def outcome_for_index(index: int) -> Outcome:
    return (Outcome.ALLOW_ONCE, Outcome.ALLOW_FOREVER, Outcome.DENY_ONCE)[index]


def update_approving(app: TermCodeApp, key: str) -> None:
    from termcode.tui.app import SessionState

    request = app.pending
    if request is None or request.respond.done():
        return
    if key in {"up", "k", "down", "j"}:
        app.approve_cursor = (
            app.approve_cursor + (-1 if key in {"up", "k"} else 1)
        ) % 3
        app._refresh_streaming_view()
        return
    shortcuts = {"1": 0, "2": 1, "3": 2, "y": 0, "n": 2, "d": 2}
    if key not in shortcuts and key not in {"enter", "space"}:
        return
    outcome = outcome_for_index(shortcuts.get(key, app.approve_cursor))
    app.pending = None
    app.state = SessionState.STREAMING
    request.respond.set_result(outcome)
    app._refresh_streaming_view()
