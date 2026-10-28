## 工具系统 Plan  
  
> 基于已批准的 spec.md。本文档与语言相关（Python 3.12+）。SDK 调用方式已对 `anthropic`（`AsyncAnthropic`，支持 tool_use streaming）、`openai`（`AsyncOpenAI`，支持 chat.completions tool_calls streaming）实测核对。  
  
## 架构概览  
  
在 ch02「provider → conversation → tui」三件套之上，新增两个包并扩展三处：  
  
- **termcode.tool（新建）**：统一工具抽象 `Tool`、执行结果 `Result`、注册中心 `Registry`、6 个核心工具。零外部依赖，不感知 LLM 协议。  
- **termcode.agent（新建）**：承载「单轮闭环」编排——请求#1（带工具）→ 收集工具调用 → 注册中心执行 → 结果回灌进 `Conversation` → 请求#2（续答）→ 最终文本 → 停。对外吐出一条 `Event` async generator 供 TUI 渲染。只依赖 `llm`、`tool`、`conversation`，不 import anthropic/openai，保持协议无关。  
- **termcode.llm（扩展）**：`Message`/`StreamEvent` 增加工具字段；新增协议无关类型 `ToolCall`/`ToolResult`/`ToolDefinition` 与 `ROLE_TOOL` 常量；`Provider.stream` 增加 `tools` 参数；两个适配器注入工具定义、解析流式工具调用、回灌工具结果。  
- **termcode.conversation（扩展）**：新增「assistant 工具调用回合」与「工具结果回合」的追加方法。  
- **termcode.prompt（扩展）**：`SYSTEM_PROMPT` 增补 Agent 角色与工具使用约定。  
- **termcode.tui（扩展）**：`submit` 改走 `Agent.run`；事件消费 task 处理工具事件；渲染 Claude Code 风格工具行与执行指示。  
- **cli.py（扩展）**：构造 `tool.new_default_registry()` 并注入 `TermCodeApp`。  
  
依赖方向（无环）：`tool → llm`；`conversation → llm`；`agent → {llm, tool, conversation}`；`tui → {agent, tool, conversation, llm, prompt}`；`llm → {config, prompt}`。  
  
## 核心数据结构  
  
### llm 包（`__init__.py` 扩展）  
  
```python  
from dataclasses import dataclass, field  
from typing import Any, AsyncIterator, Literal, Protocol  
  
# 消息角色——新增 ROLE_TOOL。  
ROLE_USER = "user"  
ROLE_ASSISTANT = "assistant"  
ROLE_TOOL = "tool"  # 携带工具执行结果的回合  
  
@dataclass  
class ToolCall:  
    """协议无关地承载模型发起的一次工具调用（流式拼接完成后）。"""  
    id: str            # provider 侧调用 id；回灌结果时配对  
    name: str          # 工具名（注册中心按名查找）  
    input: str         # 拼接完成的 JSON 参数字符串（raw JSON）  
  
@dataclass  
class ToolResult:  
    """协议无关地承载一次工具执行结果。"""  
    tool_call_id: str  # 对应 ToolCall.id  
    content: str       # 执行产出（成功内容或结构化错误文本）  
    is_error: bool = False  # 是否为错误结果（F9）  
  
@dataclass  
class ToolDefinition:  
    """注册中心导出的协议无关工具定义。"""  
    name: str  
    description: str  
    input_schema: dict[str, Any]  # 完整 JSON Schema：type/properties/required  
  
# Message 扩展：assistant 回合可带 tool_calls；ROLE_TOOL 回合带 tool_results。  
@dataclass  
class Message:  
    role: Literal["user", "assistant", "tool"]  
    content: str = ""  
    tool_calls: list[ToolCall] = field(default_factory=list)    # 仅 assistant  
    tool_results: list[ToolResult] = field(default_factory=list) # 仅 ROLE_TOOL  
  
# StreamEvent 扩展：在 text/done/err 之外，turn 结束时一次性上抛 tool_calls。  
@dataclass  
class StreamEvent:  
    text: str = ""                       # 文本增量  
    tool_calls: list[ToolCall] = field(default_factory=list)  # 非空：本轮模型请求执行这些工具（done 之前发出）  
    done: bool = False  
    err: Exception | None = None  
```  
  
`Provider.stream` 签名变更：  
  
```python  
class Provider(Protocol):  
    @property  
    def name(self) -> str: ...  
    @property  
    def model(self) -> str: ...  
    def stream(  
        self,  
        msgs: list[Message],  
        tools: list[ToolDefinition],  
    ) -> AsyncIterator[StreamEvent]: ...  
```  
  
`tools` 为空表示本次请求不带工具。续答请求（请求#2）仍传入 `tools`（与真实协议一致），但编排层忽略其再次返回的工具调用（单轮）。  
  
### tool 包（新建）  
  
```python  
from dataclasses import dataclass  
from typing import Any, Protocol, runtime_checkable  
  
@dataclass  
class Result:  
    """工具执行结果——永远以值类型返回，从不抛 Python 异常给上层。"""  
    content: str          # 回灌给模型的文本（已截断/带行号等）  
    is_error: bool = False  # True 表示结构化错误，content 即错误描述  
  
@runtime_checkable  
class Tool(Protocol):  
    """统一工具抽象（F1）。"""  
    def name(self) -> str: ...               # 模型看到的工具名，如 "read_file"  
    def description(self) -> str: ...        # 给模型的用途说明  
    def parameters(self) -> dict[str, Any]: ...  # 手写 JSON Schema（type/properties/required/description）  
    async def execute(self, args: str) -> Result: ...  
    # 注：args 是 raw JSON 字符串；超时由外部 asyncio.wait_for 控制  
```  
  
### agent 包（新建）  
  
```python  
from dataclasses import dataclass, field  
from enum import Enum  
from typing import AsyncIterator  
  
class Phase(Enum):  
    START = "start"  # 工具开始执行  
    END = "end"      # 工具执行完毕  
  
@dataclass  
class ToolEvent:  
    """一次工具调用的开始/结束（供 TUI 渲染工具行与结果摘要）。"""  
    name: str  
    args: str = ""        # 参数预览（用于 ● name(args)）  
    phase: Phase = Phase.START  
    result: str = ""      # phase=END：结果摘要  
    is_error: bool = False  # phase=END：是否错误  
  
@dataclass  
class Event:  
    """单轮闭环对外事件流元素，TUI 据非 None 字段分派渲染。"""  
    text: str = ""          # 文本增量（preamble 或最终答复）  
    tool: ToolEvent | None = None  # 工具调用开始/结束  
    done: bool = False      # 本轮结束  
    err: Exception | None = None  # 出错（不中断会话）  
  
class Agent:  
    """持有 provider 与注册中心，执行单轮闭环。"""  
    def __init__(self, provider: Provider, registry: Registry) -> None:  
        self._provider = provider  
        self._registry = registry  
  
    async def run(self, conv: Conversation) -> AsyncIterator[Event]:  
        """执行单轮闭环，async generator 吐出事件流。调用方 cancel() 该 task 即终止。"""  
        ...  
```  
  
### 模块设计  
  
#### `termcode.tool`  
  
**职责：** 提供 6 个工具的统一抽象与执行；集中登记与导出；所有失败包成 `Result(is_error=True)` 而非抛异常（F1/F2/F9/N4）。  
  
**对外接口：** `Tool`、`Result`、`Registry`、`new_default_registry`、`DEFAULT_TIMEOUT`。  
  
**依赖：** 标准库（`pathlib`、`asyncio`、`re`、`fnmatch`、`json`）、`termcode.llm`（仅为 `definitions()` 返回 `list[ToolDefinition]`）。  
  
**关键实现点：**  
- Schema 手写为 `dict[str, Any]`：OpenAI 直接用整对象；Anthropic 由 llm 适配器取 `["properties"]`/`["required"]`。  
- `read_file` 带行号、行/字节上限、`[truncated]` 标注（N5/AC2）。  
- `edit_file` 唯一匹配语义 + 含计数的可区分错误（AC4）。  
- `bash` 用 `asyncio.create_subprocess_shell(cmd, stdout=PIPE, stderr=PIPE)`，外层 `asyncio.wait_for(..., timeout=DEFAULT_TIMEOUT)` 控制超时；超时则 `proc.kill()` 并返回结构化错误（AC5/N1）。`shell=True` 自带 pipe/redirect 支持，跨平台用 `/bin/sh -c` 或 `cmd /C`（asyncio 自动按 OS 选）。  
- `glob` 用 `pathlib.Path(root).glob(pattern)` 或自实现 `**` 段匹配；遍历期间 `await asyncio.sleep(0)` 让出 event loop。`grep` 用 `re` + `Path.rglob` + 异步友好的分批读取。  
- 空 `args`（OpenAI 可能给空串而非 `{}`）归一为 `"{}"` 处理，避免误报参数错误。  
  
#### `termcode.agent`  
  
**职责：** 单轮闭环编排（F5/F6），保证 AC9 单轮上限；把 provider 的 `StreamEvent` 与工具执行翻译成统一 `Event` 异步流。  
  
**对外接口：** `Agent`、`Event`、`ToolEvent`、`Phase`。  
  
**依赖：** `termcode.llm`、`termcode.tool`、`termcode.conversation`、`asyncio`。  
  
**run 算法：**  
1. `defs = self._registry.definitions()`。  
2. **请求#1**：`async for ev in self._stream_once(conv, defs):` 转发 `text` 增量给调用方、累积完整 preamble 文本、收集 `tool_calls`；出错则 `yield Event(err=...)` 后结束。  
3. 若无 `tool_calls`：`conv.add_assistant(preamble)`，`yield Event(done=True)`，结束（纯文本回合，与 ch02 等价）。  
4. 有 `tool_calls`：`conv.add_assistant_with_tool_calls(preamble, calls)`。  
5. 顺序执行每个 call：`yield Event(tool=ToolEvent(name, args, Phase.START))` → `r = await self._registry.execute(call.name, call.input, timeout=tool.DEFAULT_TIMEOUT)` → `yield Event(tool=ToolEvent(name, phase=Phase.END, result=r.content, is_error=r.is_error)` → 收集 `ToolResult(tool_call_id=call.id, content=r.content, is_error=r.is_error)`。  
6. `conv.add_tool_results(results)`。  
7. **请求#2**：`async for ev in self._stream_once(conv, defs):` 转发最终答复 `text`、累积 final 文本；**忽略**其返回的任何 `tool_calls`（单轮，AC9）。  
8. `conv.add_assistant(final)`，`yield Event(done=True)`。  
  
- 调用方 `cancel()` 此 task（退出/Ctrl+C）时 `async for` 自然抛 `CancelledError`，沿向上传播终止；工具执行经 `asyncio.wait_for` 受 `DEFAULT_TIMEOUT` 约束（N1）。  
  
### `termcode.llm`（扩展）  
  
**职责：** 协议无关请求/响应抽象 + 两协议工具调用全流程（F3/F4/F6/F7）。  
  
**`anthropic_provider.py` 关键改动：**  
- 请求构造加 `params["tools"] = to_anthropic_tools(tools)`：每项 `{"name": d.name, "description": d.description, "input_schema": d.input_schema}`（SDK 直接接受 `input_schema` 完整对象）。  
- 流循环用 `async with self._client.messages.stream(**params) as stream:`；按 `event.type` 分派：`content_block_delta` + `delta.type == "text_delta"` → `yield StreamEvent(text=delta.text)`；`thinking_delta` / `input_json_delta` 跳过（SDK 内部累加器会保留完整 input JSON）。  
- 流结束后取 `final_message = await stream.get_final_message()`：若 `final_message.stop_reason == "tool_use"`，遍历 `final_message.content`，对 `ToolUseBlock` 类型块收集 `ToolCall(id=block.id, name=block.name, input=json.dumps(block.input))`，`yield StreamEvent(tool_calls=calls)`。  
- `to_anthropic_messages` 扩展：assistant 回合若有 `tool_calls`，content 用 `[{"type": "text", "text": preamble}, {"type": "tool_use", "id": ..., "name": ..., "input": json.loads(call.input)}]` 数组；`ROLE_TOOL` 回合把每个 `ToolResult` 用 `{"type": "tool_result", "tool_use_id": id, "content": content, "is_error": is_error}` 拼进**一条 user 消息**的 content 数组。  
  
**`openai_provider.py` 关键改动：**  
- 请求构造加 `params["tools"]`：每项 `{"type": "function", "function": {"name": d.name, "description": d.description, "parameters": d.input_schema}}`。  
- 流循环 `async for chunk in await self._client.chat.completions.create(..., stream=True):`；按 index 维护 `tool_calls_buf: dict[int, dict]`，把 `delta.tool_calls[i].function.name` 和 `delta.tool_calls[i].function.arguments` 累加合并（id/name 取首次出现，arguments 拼接）；正文 `delta.content` 仍 yield 文本增量。  
- 流结束后（`finish_reason == "tool_calls"` 或 buf 非空）按 index 排序组 `ToolCall(id, name, input=arguments_buf)`（空 arguments 归一为 `"{}"`），`yield StreamEvent(tool_calls=calls)`。  
- `to_openai_messages` 扩展：assistant 回合若有 `tool_calls`，发 `{"role": "assistant", "content": preamble or None, "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.input}} for c in calls]}`；`ROLE_TOOL` 回合每个 `ToolResult` 发一条 `{"role": "tool", "tool_call_id": r.tool_call_id, "content": r.content}`。  
  
### `termcode.conversation`（扩展）  
  
```python  
def add_assistant_with_tool_calls(self, text: str, calls: list[ToolCall]) -> None:  
    """assistant 工具调用回合。"""  
    self._messages.append(Message(role=ROLE_ASSISTANT, content=text, tool_calls=list(calls)))  
  
def add_tool_results(self, results: list[ToolResult]) -> None:  
    """ROLE_TOOL 结果回合。"""  
    self._messages.append(Message(role=ROLE_TOOL, tool_results=list(results)))  
```  
  
保留 `add_user`/`add_assistant`/`messages`/`__len__` 不变。  
  
### `termcode.tui`（扩展）  
  
**职责：** 渲染 `agent.Event`（文本/工具行/结果摘要/错误/结束），保持非阻塞（N2）。  
  
- `TermCodeApp.__init__(self, providers, version, registry)`：存 `self._registry`。  
- 新增 reactive / 成员：`self._cur_tool: ToolDisplay | None`（执行中指示：name/args，非 None 即在 `#streaming` 渲染执行行）。  
- `submit`：`conv.add_user(text)` 后 `self._stream_task = asyncio.create_task(self._consume_agent_events())`，task 内构造 `agent = Agent(self.provider, self._registry)` 后 `async for ev in agent.run(self.conv):` 分派。  
- `_consume_agent_events` 分派：  
  - `ev.text` 非空：`cur_reply += ev.text`，更新动态区显示；  
  - `ev.tool` 且 `phase==START`：若 `cur_reply` 非空，先把 preamble 经 markdown 渲染后 `RichLog.write(...)` 提交并清空 `cur_reply`；置 `self._cur_tool`；  
  - `ev.tool` 且 `phase==END`：依次 `RichLog.write(tool_line(name, args))` + `RichLog.write(tool_result_summary(result, is_error))` 顺序提交；清 `self._cur_tool`；  
  - `ev.done`：把 `cur_reply`（最终答复）经 `rich.markdown.Markdown` 渲染并写入 `RichLog`；`_finish_turn()`；  
  - `ev.err`：`RichLog.write(error_block(err))`；`_finish_turn()`。  
- `view.py` 新增：`tool_line(name, args) -> RenderableType`（青/绿 `●` + `name(args)`，用 `Text(..., style="bold cyan")`）、`tool_result_summary(result, is_error) -> RenderableType`（缩进 `  ⎿ `、灰/红、UI 截断 ~8 行）。  
- `View.compose`（或 `_render_streaming`）在 `self._cur_tool is not None` 时渲染「`● name(args)` + spinner Running…」到 `#streaming`，否则沿用「Imagining… (Ns)」。  
- `RichLog.write` 同步追加保证顺序——Python 这边只有一个 event loop，不存在 Go `tea.Batch` 那种并发乱序问题，无需特殊同步原语。  
  
## 模块交互  
  
```  
用户提交  
  └─ TermCodeApp.submit: conv.add_user(text); self._stream_task = asyncio.create_task(self._consume_agent_events())  
        └─ _consume_agent_events:  
             └─ agent = Agent(provider, registry); async for ev in agent.run(conv):  
                  ├─ 请求#1: async for se in provider.stream(conv.messages(), registry.definitions()):  
                  │     └─ 适配器: 注入 tools → 流式拼接 → StreamEvent{text…} / StreamEvent{tool_calls}  
                  │     → agent 转发 Event{text}（preamble），收集 calls  
                  ├─ 无 calls → conv.add_assistant(preamble); yield Event(done=True)  
                  └─ 有 calls:  
                       ├─ conv.add_assistant_with_tool_calls(preamble, calls)  
                       ├─ for call: yield Event(tool=START) → await registry.execute(name, args, timeout=DEFAULT_TIMEOUT) → yield Event(tool=END)  
                       ├─ conv.add_tool_results(results)  
                       ├─ 请求#2: async for se in provider.stream(...) → yield Event(text)（最终答复）  
                       │     （适配器把 conv 里的 tool_use/tool_result 回合映射为各自线格式）  
                       └─ conv.add_assistant(final); yield Event(done=True)  
  └─ _consume_agent_events 按 Event 类型渲染（cur_reply 动态区 / RichLog.write 进 scrollback）  
```  
  
并发：`conv` 仅在单个 event loop 上被消费 task 触碰——`submit` 在 create_task 前 `add_user`，之后只读；`run` 协程独占后续所有 `conv` 变更。`messages()` 返回副本。Textual UI 渲染回到主协程序列化执行，与 `conv` 互不干扰（N2）。

 工具系统 Plan

> 基于已批准的 spec.md。本文档与语言相关（Python 3.12+）。SDK 调用方式已对 `anthropic`（`AsyncAnthropic`，支持 tool_use streaming）、`openai`（`AsyncOpenAI`，支持 chat.completions tool_calls streaming）实测核对。

## 架构概览

在 ch02「provider → conversation → tui」三件套之上，新增两个包并扩展三处：

- **termcode.tool（新建）**：统一工具抽象 `Tool`、执行结果 `Result`、注册中心 `Registry`、6 个核心工具。零外部依赖，不感知 LLM 协议。
- **termcode.agent（新建）**：承载「单轮闭环」编排——请求#1（带工具）→ 收集工具调用 → 注册中心执行 → 结果回灌进 `Conversation` → 请求#2（续答）→ 最终文本 → 停。对外吐出一条 `Event` async generator 供 TUI 渲染。只依赖 `llm`、`tool`、`conversation`，不 import anthropic/openai，保持协议无关。
- **termcode.llm（扩展）**：`Message`/`StreamEvent` 增加工具字段；新增协议无关类型 `ToolCall`/`ToolResult`/`ToolDefinition` 与 `ROLE_TOOL` 常量；`Provider.stream` 增加 `tools` 参数；两个适配器注入工具定义、解析流式工具调用、回灌工具结果。
- **termcode.conversation（扩展）**：新增「assistant 工具调用回合」与「工具结果回合」的追加方法。
- **termcode.prompt（扩展）**：`SYSTEM_PROMPT` 增补 Agent 角色与工具使用约定。
- **termcode.tui（扩展）**：`submit` 改走 `Agent.run`；事件消费 task 处理工具事件；渲染 Claude Code 风格工具行与执行指示。
- **cli.py（扩展）**：构造 `tool.new_default_registry()` 并注入 `TermCodeApp`。


依赖方向（无环）：`tool → llm`；`conversation → llm`；`agent → {llm, tool, conversation}`；`tui → {agent, tool, conversation, llm, prompt}`；`llm → {config, prompt}`。


## 核心数据结构

### llm 包（`__init__.py` 扩展）

```python
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol

# 消息角色——新增 ROLE_TOOL。
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"  # 携带工具执行结果的回合

@dataclass
class ToolCall:
    """协议无关地承载模型发起的一次工具调用（流式拼接完成后）。"""
    id: str            # provider 侧调用 id；回灌结果时配对
    name: str          # 工具名（注册中心按名查找）
    input: str         # 拼接完成的 JSON 参数字符串（raw JSON）

@dataclass
class ToolResult:
    """协议无关地承载一次工具执行结果。"""
    tool_call_id: str  # 对应 ToolCall.id
    content: str       # 执行产出（成功内容或结构化错误文本）
    is_error: bool = False  # 是否为错误结果（F9）

@dataclass
class ToolDefinition:
    """注册中心导出的协议无关工具定义。"""
    name: str
    description: str
    input_schema: dict[str, Any]  # 完整 JSON Schema：type/properties/required


# Message 扩展：assistant 回合可带 tool_calls；ROLE_TOOL 回合带 tool_results。
@dataclass
class Message:
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)    # 仅 assistant
    tool_results: list[ToolResult] = field(default_factory=list) # 仅 ROLE_TOOL


# StreamEvent 扩展：在 text/done/err 之外，turn 结束时一次性上抛 tool_calls。
@dataclass
class StreamEvent:
    text: str = ""                       # 文本增量
    tool_calls: list[ToolCall] = field(default_factory=list)  # 非空：本轮模型请求执行这些工具（done 之前发出）
    done: bool = False
    err: Exception | None = None



`Provider.stream` 签名变更：

```python
class Provider(Protocol):
    @property
def name(self) -> str: ...
    @property
def model(self) -> str: ...
    def stream(
        self,
        msgs: list[Message],
        tools: list[ToolDefinition],
    ) -> AsyncIterator[StreamEvent]: ...
```

`tools` 为空表示本次请求不带工具。续答请求（请求#2）仍传入 `tools`（与真实协议一致），但编排层忽略其再次返回的工具调用（单轮）。

### tool 包（新建）

```python
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

@dataclass
class Result:
    """工具执行结果——永远以值类型返回，从不抛 Python 异常给上层。"""
    content: str          # 回灌给模型的文本（已截断/带行号等）
    is_error: bool = False  # True 表示结构化错误，content 即错误描述

@runtime_checkable
class Tool(Protocol):
    """统一工具抽象（F1）。"""
    def name(self) -> str: ...
    def description(self) -> str: ...
    def parameters(self) -> dict[str, Any]: ...
    async def execute(self, args: str) -> Result: ...


class Registry:
    """集中登记、按名查找、导出定义、按名执行。"""
    def __init__(self) -> None:
        self._order: list[str] = []      # 保持注册顺序，导出稳定
        self._tools: dict[str, Tool] = {}

    def register(self, t: Tool) -> None: ...
    def get(self, name: str) -> Tool | None: ...
    def definitions(self) -> list["ToolDefinition"]: ...  # F3/AC1：按序导出
    async def execute(self, name: str, args: str, timeout: float) -> Result: ...

# F5/F9：未知工具兜底为 is_error；超时由 asyncio.wait_for 抛 TimeoutError → 转 Result

def new_default_registry() -> Registry:
    """构造并注册 6 个工具，固化 bash 超时与各上限常量。"""
    ...

DEFAULT_TIMEOUT: float = 30.0  # 单个工具执行的默认超时秒数（N1，不可配）

"""
```

&nbsp;