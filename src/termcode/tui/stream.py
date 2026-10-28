"""流事件消费与计时；网络等待始终让出事件循环。"""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from termcode.tui.app import TermCodeApp


async def consume_stream(app: TermCodeApp) -> None:
    if app.provider is None:
        app._finish_with_error(RuntimeError("尚未选择供应商"))
        return
    try:
        async with aclosing(app.provider.stream(app.conv.messages())) as stream:
            async for event in stream:
                if event.err is not None:
                    app._finish_with_error(event.err)
                    return
                if event.text:
                    app.cur_reply += event.text
                    app._refresh_streaming_view()
                if event.done:
                    app._finish_with_assistant(app.cur_reply)
                    return
        app._finish_with_error(RuntimeError("响应流未正常结束，请重新发送问题"))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        app._finish_with_error(exc)


def tick(app: TermCodeApp) -> None:
    app._refresh_streaming_view()
