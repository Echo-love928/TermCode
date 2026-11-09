"""验证 ch13 的角色、权限、运行循环与任务交接。"""

import asyncio
import json
from dataclasses import fields
from pathlib import Path

import pytest

from termcode.agent import Agent, ApprovalRequest, Event, MaxTurnsReached
from termcode.agent.agent_tool import AgentTool
from termcode.agent.fork import FORK_BOILERPLATE, build_forked_messages
from termcode.config.config import ConfigError, ProviderConfig, load
from termcode.conversation import Conversation
from termcode.llm import Message, StreamEvent, ToolCall, Usage
from termcode.permission import Decision, Engine, Mode, Outcome
from termcode.permission.rule import parse_rule
from termcode.prompt.environment import Environment
from termcode.subagent import Definition, Source, load_catalog, parse_definition
from termcode.task import (
    Manager,
    SendMessageTool,
    Status,
    TaskGetTool,
    TaskListTool,
    TaskStopTool,
)
from termcode.tool import new_default_registry
from termcode.tool.filter import FilterParams, apply_agent_tool_filter


class Provider:
    name = "fixture"
    model = "fixture"

    def __init__(self, *events):
        self.events = iter(events)
        self.requests = []
        self.closed = False

    async def aclose(self):
        self.closed = True

    async def stream(self, req):
        self.requests.append(req)
        event = next(self.events)
        if isinstance(event, asyncio.Event):
            await event.wait()
            event = next(self.events)
        yield event


def call(name, **args):
    return StreamEvent(
        done=True, tool_calls=[ToolCall("call-1", name, json.dumps(args))]
    )


def payload(role="Explore", **options):
    return json.dumps(
        {
            "prompt": "读取文档",
            "description": "测试任务",
            "subagent_type": role,
            **options,
        }
    )


def role_file(root, filename, content):
    path = root / ".termcode/agents" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_catalog_layers_and_fallback(tmp_path, monkeypatch, capsys):
    assert {item.name for item in fields(Definition)} == {
        "name",
        "description",
        "tools",
        "disallowed_tools",
        "model",
        "max_turns",
        "permission_mode",
        "dont_ask",
        "background",
        "system_prompt",
        "file_path",
        "source",
        "isolation",
    }
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    role_file(home, "explore.md", "---\nname: Explore\ndescription: user\n---\nuser")
    role_file(
        tmp_path,
        "explore.md",
        "\ufeff---\nname: explore\ndescription: project\n"
        "tools: [read_file]\ndisallowedTools: [edit_file]\nmodel: gpt-4\n"
        "maxTurns: 5\npermissionMode: weirdMode\nbackground: true\n---\nproject",
    )
    role_file(tmp_path, "broken.md", "---\nname: broken\n---")
    catalog = load_catalog(tmp_path)
    assert {entry.name for entry in catalog.list_by_source(Source.BUILTIN)} == {
        "Explore",
        "Plan",
        "general-purpose",
    }
    assert catalog.list_by_source(Source.USER)[0].system_prompt == "user"
    role = catalog.resolve("EXPLORE")
    assert role.source == Source.PROJECT and role.system_prompt == "project"
    assert role.tools == ["read_file"] and role.disallowed_tools == ["edit_file"]
    assert role.model == "inherit" and role.permission_mode == Mode.DEFAULT
    assert role.max_turns == 5 and role.background
    assert catalog.resolve("broken") is None and catalog.fork_definition().is_fork()
    warnings = capsys.readouterr().err
    assert (
        'unknown model "gpt-4"' in warnings
        and 'unknown permissionMode "weirdMode"' in warnings
    )
    assert "broken.md" in warnings


@pytest.mark.parametrize(
    "meta",
    [
        "description: missing",
        "name: missing",
        "name: x\ndescription: x\ntools: read_file",
        "name: x\ndescription: x\nmaxTurns: true",
    ],
)
def test_invalid_definition(meta):
    with pytest.raises(ValueError):
        parse_definition(f"---\n{meta}\n---".encode(), "x.md", Source.PROJECT)


def test_filter_and_fork_history():
    tools = [
        "Agent",
        "TaskList",
        "read_file",
        "edit_file",
        "mcp__server__read",
        "LoadSkill",
    ]
    assert apply_agent_tool_filter(
        FilterParams(
            tools,
            Source.USER,
            True,
            allowed=[
                "Agent",
                "TaskList",
                "read_file",
                "edit_file",
                "mcp__server__read",
            ],
            disallowed=["edit_file"],
        )
    ) == ["read_file", "mcp__server__read"]
    assert apply_agent_tool_filter(
        FilterParams(tools, Source.BUILTIN, True, fork=True)
    ) == ["Agent", *tools[2:]]
    parent = [
        Message("user", "history"),
        Message("assistant", tool_calls=[ToolCall("id", "Agent", "{}")]),
    ]
    fork = build_forked_messages(parent, "job")
    assert fork[:2] == parent and fork[1].tool_calls is not parent[1].tool_calls
    assert fork[2].tool_results[0].tool_call_id == "id"
    assert fork[-1].content == FORK_BOILERPLATE + "job"


@pytest.mark.parametrize("pattern", ["=Explore", "EXP*", "~^explore$", "!~^plan$"])
def test_role_permission_matches_catalog_aliases(tmp_path, pattern):
    engine = Engine(str(tmp_path))
    rule, error = parse_rule(f"Agent({pattern})")
    assert rule is not None and error is None
    engine.local.deny.append(rule)
    call = ToolCall("id", "Agent", payload("  eXplOrE  "))
    assert engine.check(Mode.BYPASS, call)[0] == Decision.DENY


def test_dont_ask_keeps_explicit_rules_and_sandbox(tmp_path):
    engine = Engine(str(tmp_path))
    rule, error = parse_rule("Bash(=echo approve)")
    assert rule is not None and error is None
    engine.local.ask.append(rule)
    for tool, args, decision in (
        ("bash", {"command": "echo approve"}, Decision.ASK),
        ("bash", {"command": "echo default"}, Decision.ALLOW),
        ("bash", {"command": "rm -rf /"}, Decision.DENY),
        ("read_file", {"path": "../outside.txt"}, Decision.DENY),
    ):
        call = ToolCall("id", tool, json.dumps(args))
        assert engine.check(Mode.DEFAULT, call, dont_ask=True)[0] == decision


async def test_completion_events_permission_and_limit(tmp_path):
    role = parse_definition(
        b"---\nname: x\ndescription: x\npermissionMode: dontAsk\n---\nrole",
        "x.md",
        Source.PROJECT,
    )
    assert role.dont_ask
    provider = Provider(
        call("bash", command="echo hello"), StreamEvent(done=True, text="final")
    )
    agent = Agent(
        provider,
        new_default_registry(),
        engine=Engine(str(tmp_path)),
        system_prompt=role.system_prompt,
        max_turns=2,
        dont_ask=role.dont_ask,
    )
    events = asyncio.Queue()
    conv = Conversation()
    assert await agent.run_to_completion(conv, "job", events) == "final"
    assert provider.requests[0].system.stable == "role"
    assert "hello" in provider.requests[1].messages[-1].tool_results[0].content
    seen = [events.get_nowait() for _ in range(events.qsize())]
    assert not any(isinstance(event, ApprovalRequest) for event in seen)
    assert any(isinstance(event, Event) and event.tool for event in seen)

    approvals = []

    async def approve(request):
        approvals.append(request)
        return Outcome.ALLOW_ONCE, True

    agent = Agent(
        Provider(call("bash", command="echo approved")),
        new_default_registry(),
        engine=Engine(str(tmp_path)),
        max_turns=1,
        approval_upgrader=approve,
        label="x",
    )
    with pytest.raises(MaxTurnsReached):
        await agent.run_to_completion(Conversation(), "job")
    assert "[来自 SubAgent x]" in approvals[0].reason


async def test_agent_tool_foreground_and_nested_guards(tmp_path):
    provider = Provider(
        call("Agent", prompt="nested", description="nested"),
        StreamEvent(done=True, text="child final"),
    )
    registry = new_default_registry()
    manager = Manager()
    tool = AgentTool(load_catalog(tmp_path), manager)
    registry.register(tool)
    parent = Agent(provider, registry, engine=Engine(str(tmp_path)))
    tool.set_parent(parent)
    assert set(tool.parameters()["properties"]) == {
        "prompt",
        "description",
        "subagent_type",
        "run_in_background",
        "model",
        "name",
    }
    assert (await tool.execute("{}")).is_error
    assert "未知 subagent_type" in (await tool.execute(payload("missing"))).content
    assert (await tool.execute(payload())).content == "child final"
    assert "Agent" not in {definition.name for definition in provider.requests[0].tools}
    assert "不能再启动" in provider.requests[1].messages[-1].tool_results[0].content
    assert "Agent" in parent.tool_names()
    assert (
        await AgentTool(tool.catalog, manager, parent, False).execute(payload(""))
    ).is_error
    assert (
        await AgentTool(tool.catalog, manager, parent, False).execute(
            payload(run_in_background=True)
        )
    ).is_error
    forged = Conversation.from_messages([Message("user", FORK_BOILERPLATE)])
    assert (
        await tool.execute_for_turn(
            payload(), parent, forged, Mode.DEFAULT, lambda _: None
        )
    ).is_error


@pytest.mark.parametrize("manual", [False, True])
async def test_detach_reuses_run_and_routes_approval(tmp_path, monkeypatch, manual):
    monkeypatch.setattr(
        "termcode.prompt.gather_environment",
        lambda version, model: Environment(
            str(tmp_path), "test", "", "", version, model
        ),
    )
    if not manual:
        monkeypatch.setattr("termcode.agent.agent_tool.AUTO_BACKGROUND_SECONDS", 0.2)
    provider = Provider(
        call("bash", command="echo continue"), StreamEvent(done=True, text="child")
    )
    manager = Manager()
    parent = Agent(provider, new_default_registry(), engine=Engine(str(tmp_path)))
    tool = AgentTool(load_catalog(tmp_path), manager)
    inline, background = asyncio.Queue(), asyncio.Queue()
    manager.approval_sink = background.put_nowait
    result = asyncio.create_task(
        tool.execute_for_turn(
            payload("general-purpose"),
            parent,
            Conversation(),
            Mode.DEFAULT,
            inline.put_nowait,
        )
    )
    try:
        approval = await asyncio.wait_for(inline.get(), 10)
        if manual:
            assert manager.request_detach()
        launched = json.loads((await result).content)
        assert launched["status"] == (
            "detached_to_background" if manual else "timed_out_to_background"
        )
        assert await asyncio.wait_for(background.get(), 10) is approval
        approval.respond.set_result(Outcome.ALLOW_ONCE)
        task_id = await asyncio.wait_for(manager.subscribe_done().get(), 10)
        assert task_id == launched["task_id"]
        assert manager.get(task_id).result == "child" and len(provider.requests) == 2
        assert "Result: child" in manager.take_notification(task_id)
    finally:
        result.cancel()
        await asyncio.gather(result, return_exceptions=True)
        await manager.clear()


async def test_background_task_tools_cancel_failure_resume(tmp_path):
    manager = Manager()
    try:
        provider = Provider(
            StreamEvent(done=True, text="first", usage=Usage(3, 2)),
            StreamEvent(done=True, text="second"),
        )
        agent = Agent(provider, new_default_registry(), engine=Engine(str(tmp_path)))
        conv = Conversation()
        task_id = await manager.launch(agent, conv, "worker", "one")
        assert await asyncio.wait_for(manager.subscribe_done().get(), 10) == task_id
        task_args = json.dumps({"task_id": task_id})
        assert (await TaskGetTool(manager).execute('{"task_id":"missing"}')).is_error
        assert all(
            cls.is_system
            for cls in (TaskGetTool, TaskListTool, TaskStopTool, SendMessageTool)
        )
        assert (
            json.loads((await TaskGetTool(manager).execute(task_args)).content)[
                "usage"
            ]["input"]
            == 3
        )
        resumed = await SendMessageTool(manager).execute(
            '{"name":"worker","message":"two"}'
        )
        assert json.loads(resumed.content)["task_id"] == task_id
        assert await asyncio.wait_for(manager.subscribe_done().get(), 10) == task_id
        assert [msg.content for msg in conv.messages()] == [
            "one",
            "first",
            "two",
            "second",
        ]
        assert "Result: first" in manager.take_notification(task_id)
        assert "Result: second" in manager.take_notification(task_id)
        blocked = Agent(
            Provider(asyncio.Event()), new_default_registry(), engine=agent.engine
        )
        second_id = await manager.launch(blocked, Conversation(), "worker", "cancel me")
        listed = json.loads((await TaskListTool(manager).execute("{}")).content)
        assert [entry["status"] for entry in listed] == ["completed", "running"]
        assert (
            await SendMessageTool(manager).execute(
                '{"name":"worker","message":"three"}'
            )
        ).is_error
        assert not (
            await TaskStopTool(manager).execute(json.dumps({"task_id": second_id}))
        ).is_error
        assert await asyncio.wait_for(manager.subscribe_done().get(), 10) == second_id
        assert manager.get(second_id).status == Status.CANCELLED
        failed = Agent(
            Provider(StreamEvent(err=RuntimeError("child failure"))),
            new_default_registry(),
            engine=agent.engine,
        )
        failed_id = await manager.launch(failed, Conversation(), "", "fail")
        assert await asyncio.wait_for(manager.subscribe_done().get(), 10) == failed_id
        assert manager.get(failed_id).status == Status.FAILED
        assert "child failure" in manager.take_notification(failed_id)
    finally:
        await manager.clear()


async def test_model_override_reuses_configuration_and_hides_provider_error(
    tmp_path, monkeypatch
):
    class AuthError(Exception):
        status_code = 401

    parent_provider = Provider()
    child_provider = Provider(StreamEvent(err=AuthError("reflected-provider-secret")))
    config = ProviderConfig("haiku", "anthropic", "fixture-key", "fixture-child-model")
    registry = new_default_registry()
    parent = Agent(parent_provider, registry, engine=Engine(str(tmp_path)))
    manager = Manager()

    def build_provider(selected):
        assert selected is config
        return child_provider

    monkeypatch.setattr("termcode.agent.launch.new_provider", build_provider)
    tool = AgentTool(load_catalog(tmp_path), manager, parent, providers=(config,))
    result = await tool.execute(payload("general-purpose", model="haiku"))
    assert result.is_error and "HTTP 401" in result.content
    assert "reflected-provider-secret" not in result.content
    assert not parent_provider.requests and not parent_provider.closed
    assert child_provider.requests and child_provider.closed


def test_background_config(tmp_path):
    path = tmp_path / "config.yaml"
    providers = (
        "providers:\n"
        "  - {name: example, protocol: openai, model: model, api_key: fixture}\n"
    )
    path.write_text(providers, encoding="utf-8")
    assert load(path).enable_subagent_background
    path.write_text(providers + "enable_subagent_background: false", encoding="utf-8")
    assert not load(path).enable_subagent_background
    path.write_text(
        providers + "enable_subagent_background: disabled", encoding="utf-8"
    )
    with pytest.raises(ConfigError):
        load(path)
