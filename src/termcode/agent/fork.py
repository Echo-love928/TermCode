import copy

from termcode.llm import Message, ToolResult

FORK_BOILERPLATE_TAG = "<fork_boilerplate>"
FORK_BOILERPLATE = """<fork_boilerplate>
你是从主对话派出的子 Agent。不要调用 Agent、提问或请求确认。
直接使用工具，只执行分配的任务。最终报告以 Scope: 开头，不超过 500 字。
</fork_boilerplate>

"""


def build_forked_messages(parent_msgs: list[Message], task: str) -> list[Message]:
    messages = copy.deepcopy(parent_msgs)
    index = len(messages) - 1
    while index >= 0 and messages[index].role == "tool":
        index -= 1
    if index >= 0 and messages[index].role == "assistant":
        answered = {
            result.tool_call_id
            for msg in messages[index + 1 :]
            for result in msg.tool_results
        }
        missing = [
            ToolResult(call.id, "[forked, skipped]", True)
            for call in messages[index].tool_calls
            if call.id not in answered
        ]
        if missing:
            messages[index + 1 :] = [
                Message(
                    "tool",
                    tool_results=[
                        result
                        for msg in messages[index + 1 :]
                        for result in msg.tool_results
                    ]
                    + missing,
                )
            ]
    messages.append(Message("user", FORK_BOILERPLATE + task))
    return messages


def is_fork_context(messages: list[Message]) -> bool:
    return any(
        msg.role == "user" and FORK_BOILERPLATE_TAG in msg.content for msg in messages
    )
