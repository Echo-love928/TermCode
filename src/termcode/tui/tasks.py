"""轮询 Lead 邮箱，把队员的消息交给现有对话循环。"""

import asyncio
import logging
from typing import TYPE_CHECKING

from textual.message import Message

if TYPE_CHECKING:
    from termcode.team.manager import LeadMessage, Manager
    from termcode.tui.app import TermCodeApp


class LeadMailMessage(Message):
    pass


def build_team_update_reminder(messages: list["LeadMessage"]) -> str:
    lines = ["<team-update>"]
    for entry in messages:
        message = entry.message
        request = f"，request_id={message.request_id}" if message.request_id else ""
        approval = f"，approve={message.approve}" if message.approve is not None else ""
        heading = f"[{entry.team_name}] {message.from_}"
        lines.append(
            f"{heading}（{message.type}{request}{approval}）：{message.text[:8000]}"
        )
    return "\n".join([*lines, "</team-update>"])


async def consume_lead_mail(app: "TermCodeApp", manager: "Manager") -> None:
    while True:
        if messages := await manager.poll_lead_mailboxes():
            app.runtime.append_reminders([build_team_update_reminder(messages)])
            app.lead_mail_event.set()
            app.post_message(LeadMailMessage())
        try:
            await manager.wake_pending()
        except (OSError, ValueError, TypeError, RuntimeError):
            logging.getLogger(__name__).exception("无法唤醒队员邮箱")
        await asyncio.sleep(1)
