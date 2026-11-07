import base64
import json
from pathlib import Path
from unittest.mock import Mock

import httpx2
import pytest

from termcode.agent import Agent, ApprovalRequest, SessionRuntime
from termcode.command import NopUI, Registry, register_builtins
from termcode.command.skills import register_skill_commands, register_skill_manager
from termcode.compact import SessionContext
from termcode.conversation import Conversation
from termcode.llm import StreamEvent, ToolCall
from termcode.permission import Decision, Engine, Mode
from termcode.prompt.environment import Environment, build_environment_context
from termcode.skills import install
from termcode.skills.executor import SkillExecutor
from termcode.skills.loader import SkillLoader
from termcode.skills.parser import (
    SkillDef,
    SkillParseError,
    parse_skill_file,
    substitute_arguments,
)
from termcode.tool import Registry as ToolRegistry
from termcode.tool.install_skill import InstallSkillTool
from termcode.tool.load_skill import LoadSkill


def write_skill(path, name="test-skill", body="Echo hello", extra=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: A test skill\n{extra}---\n{body}\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def loader(tmp_path):
    return SkillLoader(tmp_path, user_dir=tmp_path / "user")


@pytest.mark.parametrize(
    "raw",
    [
        "name: test",
        "---\nname: test",
        "---\nname: [\n---\n",
        "---\n- name\n---\n",
        "---\nname: test\ndescription: desc\n123: value\n---\n",
        "---\ndescription: desc\n---\n",
        "---\nname: test\n---\n",
        "---\nname: Test_1\ndescription: desc\n---\n",
        "---\nname: test\ndescription: desc\nmode: other\n---\n",
        "---\nname: test\ndescription: desc\ncontext: other\n---\n",
        "---\nname: test\ndescription: desc\nmodel: 3\n---\n",
    ],
)
def test_parser_invalid(tmp_path, raw):
    path = tmp_path / "test.md"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(SkillParseError):
        parse_skill_file(path)


def test_parser_valid(tmp_path):
    with pytest.raises(SkillParseError):
        parse_skill_file(tmp_path / "missing.md")
    path = write_skill(tmp_path / "test.md", body="    Echo hello")
    assert parse_skill_file(path) == SkillDef(
        "test-skill", "A test skill", "    Echo hello", path
    )
    path = write_skill(
        tmp_path / "test-skill/SKILL.md",
        extra="mode: fork\ncontext: recent\nmodel: fast\n",
    )
    skill = parse_skill_file(path)
    assert (skill.is_directory, skill.mode, skill.context, skill.model) == (
        True,
        "fork",
        "recent",
        "fast",
    )


@pytest.mark.parametrize(
    ("body", "args", "expected"),
    [
        ("Do $ARGUMENTS", "it", "Do it"),
        ("Do $ARGUMENTS", "", "Do "),
        ("Do it", "other", "Do it"),
        ("$ARGUMENTS $ARGUMENTS", "it", "it it"),
    ],
)
def test_substitute_arguments(body, args, expected):
    assert substitute_arguments(body, args) == expected


def test_loader_precedence_reload_and_cache(tmp_path, loader, caplog):
    project = tmp_path / ".termcode/skills"
    path = write_skill(project / "test-skill/SKILL.md", body="project")
    write_skill(loader.user_dir / "test.md", body="user")
    write_skill(loader.user_dir / "other.md", "other")
    (project / "bad.md").write_text("broken", encoding="utf-8")
    loader.load_all()
    assert "Skipping project skill 'bad'" in caplog.text
    assert loader.get_catalog() == [
        ("other", "A test skill"),
        ("test-skill", "A test skill"),
    ]
    assert loader.get("test-skill").prompt_body == "project"
    assert loader.get("test-skill").is_directory
    assert not loader.get("other").is_directory
    assert loader.get_source_label("test-skill") == "project"
    assert loader.get_source_label("other") == "user"
    assert loader.get("missing") is None and loader.get_source_label("missing") is None
    write_skill(path, body="updated")
    assert loader.get("test-skill").prompt_body == "updated"
    path.write_text("broken again", encoding="utf-8")
    assert loader.get("test-skill").prompt_body == "updated"
    write_skill(path, "renamed")
    assert loader.get("test-skill").prompt_body == "updated"
    loader.reload()
    assert loader.get("test-skill").prompt_body == "user"
    assert loader.get_source_label("renamed") == "project"


async def test_load_skill_and_prompt(tmp_path, loader):
    path = write_skill(tmp_path / ".termcode/skills/test.md", body="Do $ARGUMENTS")
    loader.load_all()
    tool = LoadSkill()
    assert tool.read_only and tool.category == "read" and not tool.is_concurrency_safe
    args = '{"name":"test-skill"}'
    assert (await tool.execute(args)).is_error
    agent = Mock(spec=Agent)
    tool.set_loader(loader)
    tool.set_agent(agent)
    assert (await tool.execute('{"name":"missing"}')).is_error
    result = await tool.execute(args)
    assert (
        result.content
        == "Skill 'test-skill' activated. SOP pinned to environment context."
    )
    agent.activate_skill.assert_called_once_with("test-skill", "Do ")
    env = Environment(str(path.parent), "test", "", "", "", "")
    assert "Active Skills" not in build_environment_context(env, {}, "")
    text = build_environment_context(env, {"test-skill": "Do it"}, "Catalog")
    assert "Catalog\n\n## Active Skills\n\n### Skill: test-skill\n\nDo it" in text


class RecordingProvider:
    name = "test"
    model = "test-model"

    def __init__(self, *events):
        self.events = iter(events)
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        yield next(self.events)

    async def aclose(self):
        pass


def make_agent(tmp_path, provider, registry):
    return Agent(
        provider,
        registry,
        engine=Engine(str(tmp_path)),
        runtime=SessionRuntime(session=SessionContext("", str(tmp_path))),
    )


async def test_intent_load_appears_in_next_request_and_clears(tmp_path, loader):
    write_skill(tmp_path / ".termcode/skills/test.md")
    loader.load_all()
    provider = RecordingProvider(
        StreamEvent(
            done=True,
            tool_calls=[ToolCall("one", "LoadSkill", '{"name":"test-skill"}')],
        ),
        StreamEvent(done=True, text="hello"),
        StreamEvent(done=True, text="ok"),
    )
    registry = ToolRegistry()
    tool = LoadSkill()
    registry.register(tool)
    agent = make_agent(tmp_path, provider, registry)
    agent.set_skill_catalog("## Available Skills\n\n- test-skill: A test skill")
    tool.set_loader(loader)
    tool.set_agent(agent)
    conv = Conversation()
    conv.add_user("请按测试 Skill 执行")
    events = [event async for event in agent.run(conv, Mode.PLAN)]
    assert not any(isinstance(event, ApprovalRequest) for event in events)
    first, second = provider.requests
    assert "Available Skills" in first.system.environment
    assert "Echo hello" not in first.system.environment
    assert "Echo hello" in second.system.environment
    assert "Echo hello" not in second.messages[-1].tool_results[0].content
    assert agent.active_skills == {"test-skill": "Echo hello"}
    agent.clear_active_skills()
    conv.add_user("再问一个无关问题")
    _ = [event async for event in agent.run(conv)]
    assert "Active Skills" not in provider.requests[-1].system.environment


@pytest.mark.parametrize("context", ["none", "recent", "full"])
async def test_fork_context_and_isolation(tmp_path, context):
    provider = RecordingProvider(StreamEvent(done=True, text="child output"))
    registry = ToolRegistry()
    registry.register(LoadSkill())
    agent = make_agent(tmp_path, provider, registry)
    agent.activate_skill("main-skill", "main only")
    conv = Conversation()
    for i in range(4):
        conv.add_user(f"user {i}")
        conv.add_assistant(f"assistant {i}")
    before = conv.messages()
    skill = SkillDef(
        "test", "desc", "Run $ARGUMENTS", Path("test.md"), mode="fork", context=context
    )
    assert (
        await SkillExecutor(agent).execute_fork(skill, "this", conv) == "child output"
    )
    request = provider.requests[0]
    assert request.messages[-1].content == "Run this"
    if context == "none":
        assert len(request.messages) == 1
    elif context == "recent":
        assert request.messages[:-1] == before[-5:]
    else:
        assert len(request.messages) == 2
        assert request.messages[0].content.startswith(
            "## Previous conversation summary"
        )
        assert all(message.content in request.messages[0].content for message in before)
    assert "main only" not in request.system.environment
    assert "LoadSkill" not in [tool.name for tool in request.tools]
    assert conv.messages() == before and agent.active_skills == {
        "main-skill": "main only"
    }
    assert agent.runtime.turn_count == 0


async def test_natural_language_fork_returns_only_result(tmp_path, loader):
    write_skill(
        tmp_path / ".termcode/skills/fork.md",
        body="Solve $ARGUMENTS",
        extra="mode: fork\ncontext: none\n",
    )
    loader.load_all()
    call = ToolCall("one", "LoadSkill", '{"name":"test-skill"}')
    provider = RecordingProvider(
        StreamEvent(done=True, tool_calls=[call]),
        StreamEvent(done=True, text="child analysis"),
        StreamEvent(done=True, text="main response"),
    )
    registry = ToolRegistry()
    tool = LoadSkill()
    registry.register(tool)
    agent = make_agent(tmp_path, provider, registry)
    tool.set_loader(loader)
    tool.set_agent(agent)
    conv = Conversation()
    conv.add_user("请按测试流程处理目标")
    events = [event async for event in agent.run(conv)]
    assert events[-1].done
    _, child, followup = provider.requests
    assert [msg.content for msg in child.messages] == ["Solve 请按测试流程处理目标"]
    assert followup.messages[-1].tool_results[0].content.endswith("child analysis")
    assert conv.messages()[-1].content == "main response"
    assert not agent.active_skills


async def test_skill_commands_override_and_restore(tmp_path, loader):
    path = write_skill(
        tmp_path / ".termcode/skills/review.md", "review", extra="license: MIT\n"
    )
    loader.load_all()
    registry = Registry()
    register_builtins(registry)
    original = registry.lookup("review")
    refresh = lambda: register_skill_commands(registry, loader)  # noqa: E731
    register_skill_manager(registry, loader, refresh)
    refresh()
    ui = Mock(spec=NopUI)
    ui.idle.return_value = True
    command = registry.lookup("review")
    assert command.description.endswith("[skill]")
    await command.argument_handler(ui, "these changes")
    ui.run_skill.assert_called_once_with(loader.get("review"), "these changes")
    manager = registry.lookup("skill")
    await manager.argument_handler(ui, "list")
    assert "[project]" in ui.println.call_args.args[0]
    await manager.argument_handler(ui, "info review")
    assert str(path) in ui.println.call_args.args[0]
    assert "license: MIT" in ui.println.call_args.args[0]
    path.unlink()
    await manager.argument_handler(ui, "reload")
    assert registry.lookup("review") is original


@pytest.mark.parametrize(
    ("url", "source"),
    [
        (
            "https://skills.sh/org/repo/demo",
            install.SkillSource("org", "repo", slug="demo"),
        ),
        (
            "https://github.com/org/repo/tree/main/skills/demo",
            install.SkillSource("org", "repo", "main", ("skills", "demo")),
        ),
        (
            "https://raw.githubusercontent.com/org/repo/main/demo/SKILL.md",
            install.SkillSource("org", "repo", "main", ("demo",)),
        ),
    ],
)
def test_parse_skill_url(url, source):
    assert install.parse_skill_url(url) == source


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com.evil.test/org/repo/tree/main",
        "http://github.com/org/repo/tree/main",
        "https://user@github.com/org/repo/tree/main",
        "https://github.com/org/repo/tree/main/%2e%2e",
        "https://github.com/org/repo/tree/main/%2Ftmp",
        "https://github.com/org/repo/blob/main/SKILL.md",
        "https://raw.githubusercontent.com/org/repo/main/file.zip",
    ],
)
def test_reject_bad_skill_url(url):
    with pytest.raises(ValueError):
        install.parse_skill_url(url)


SKILL_TEXT = b"---\nname: demo\ndescription: Demo\n---\nDo the demo"


def file_entry(name, size=1):
    return {"name": name, "type": "file", "size": size}


def dir_entry(name):
    return {"name": name, "type": "dir"}


def github_fixture(monkeypatch, paths):
    client = httpx2.AsyncClient

    def respond(request):
        assert request.url.host == "api.github.com"
        key = request.url.path.removeprefix("/repos/org/repo/contents").strip("/")
        if key not in paths:
            return httpx2.Response(404)
        value = paths[key]
        if isinstance(value, bytes):
            value = {
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(value).decode(),
            }
        return httpx2.Response(200, json=value)

    def connect(**kwargs):
        return client(transport=httpx2.MockTransport(respond), **kwargs)

    monkeypatch.setattr(install.httpx2, "AsyncClient", connect)


async def test_install_and_reload(tmp_path, loader, monkeypatch):
    paths = {
        "skills/demo": [
            file_entry("SKILL.md", len(SKILL_TEXT)),
            dir_entry("references"),
        ],
        "skills/demo/SKILL.md": SKILL_TEXT,
        "skills/demo/references": [file_entry("helper.py")],
        "skills/demo/references/helper.py": b"x",
    }
    github_fixture(monkeypatch, paths)
    refresh = Mock()
    tool = InstallSkillTool(loader, refresh)
    url = "https://skills.sh/org/repo/demo"
    call = ToolCall("install", "InstallSkill", json.dumps({"url": url}))
    assert not tool.read_only and tool.category == "write"
    assert Engine(str(tmp_path)).check(Mode.DEFAULT, call)[0] == Decision.ASK
    assert (await tool.execute(call.input)).is_error is False
    assert loader.get("demo").prompt_body == "Do the demo"
    assert (loader.user_dir / "demo/references/helper.py").read_bytes() == b"x"
    refresh.assert_called_once()
    assert (await tool.execute(call.input)).is_error
    assert list(loader.user_dir.iterdir()) == [loader.user_dir / "demo"]


@pytest.mark.parametrize(
    "case", ["missing", "path", "type", "size", "total", "count", "depth", "invalid"]
)
async def test_install_rejects_incomplete_or_unsafe_tree(loader, monkeypatch, case):
    paths = {
        "demo": [file_entry("SKILL.md", len(SKILL_TEXT))],
        "demo/SKILL.md": SKILL_TEXT,
    }
    if case == "missing":
        paths["demo"] = []
    elif case == "path":
        paths["demo"].append(file_entry("../outside"))
    elif case == "type":
        paths["demo"].append({"name": "link", "type": "symlink"})
    elif case == "size":
        paths["demo"].append(file_entry("large", install.MAX_FILE_SIZE + 1))
    elif case == "total":
        monkeypatch.setattr(install, "MAX_TOTAL_SIZE", len(SKILL_TEXT) + 1)
        paths["demo"].append(file_entry("too-large", 2))
    elif case == "count":
        monkeypatch.setattr(install, "MAX_FILE_COUNT", 1)
        paths["demo"].append(file_entry("extra"))
    elif case == "depth":
        monkeypatch.setattr(install, "MAX_RECURSION_DEPTH", 0)
        paths["demo"].append(dir_entry("references"))
    else:
        paths["demo/SKILL.md"] = b"malformed"
    github_fixture(monkeypatch, paths)
    with pytest.raises(ValueError):
        await install.install_skill(
            install.SkillSource("org", "repo", path=("demo",)), loader.user_dir
        )
    assert not list(loader.user_dir.iterdir())
