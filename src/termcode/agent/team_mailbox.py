"""队员在每次请求模型前消费自己的团队消息。"""

from termcode.agent.team_hook import TeammateContext
from termcode.conversation import Conversation
from termcode.permission import Mode
from termcode.team.mailbox import MessageType

from .agent import Agent


async def ingest_team_mailbox(
    agent: Agent, context: TeammateContext, conv: Conversation
) -> None:
    indices, messages = await context.mailbox.read_unread(context.agent_id)
    if not messages:
        return
    lines = ["<incoming-messages>", f"收到 {len(messages)} 条新消息："]
    mode = agent.permission_mode
    for index, message in enumerate(messages, 1):
        details = f", request_id={message.request_id}" if message.request_id else ""
        lines.append(
            f"[{index}] 来自 {message.from_}"
            f"（type={message.type}, ts={message.timestamp}{details}）：{message.text}"
        )
        if (
            message.type == MessageType.PLAN_APPROVAL_RESPONSE
            and message.from_ == "lead"
            and message.approve is not None
        ):
            if message.approve:
                mode = Mode.DEFAULT
                lines.append("Lead 已批准计划，权限已切换到 default，可以执行计划。")
            else:
                mode = Mode.PLAN
                lines.append("Lead 已驳回计划，请根据反馈修改后重新提交。")
    lines.append("</incoming-messages>")
    agent.permission_mode = mode
    # 随会话存档，后续工具轮与续派仍能看到任务正文和审批反馈。
    conv.add_user("\n".join(lines))
    await context.mailbox.mark_read(context.agent_id, indices)
