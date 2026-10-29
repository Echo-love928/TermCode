"""通过已配置模型验证多轮请求，并打印缓存写入/读取用量。"""

import argparse
import asyncio
import hashlib
import json
import uuid
from collections.abc import Callable
from contextlib import aclosing
from dataclasses import asdict, replace
from pathlib import Path

from termcode import __version__
from termcode.agent import Agent, Mode
from termcode.config import load
from termcode.conversation import Conversation
from termcode.llm import Message, Provider, Request, System, Usage, new_provider
from termcode.tool import new_default_registry


async def check_cache(provider: Provider, output: Callable[[str], None]) -> int:
    """用独立的人工前缀验证缓存；不发送项目提示、环境或工具定义。"""
    stable = f"Synthetic cache diagnostic {uuid.uuid4().hex}. Reply only OK.\n"
    stable += "\n".join(
        f"Sample record {i:04d}: alpha beta gamma delta epsilon zeta eta theta "
        "iota kappa lambda mu nu xi omicron pi rho sigma tau."
        for i in range(256)
    )
    messages = [Message("user", "Reply only OK.")]
    usages: list[Usage] = []
    for turn in (1, 2):
        usage = None
        reply = ""
        done = False
        async with aclosing(
            provider.stream(Request(messages, system=System(stable)))
        ) as stream:
            async for event in stream:
                if event.err:
                    raise RuntimeError("缓存诊断请求失败，请检查服务状态")
                if event.usage:
                    usage = event.usage
                reply += event.text
                done = done or event.done
        if not done or usage is None:
            raise RuntimeError("缓存诊断未收到完整响应或用量，无法判断")
        usages.append(usage)
        output(json.dumps({"turn": turn, "usage": asdict(usage)}, ensure_ascii=False))
        # 下一轮使用全新的列表，保留首轮请求快照，且两轮前缀字节完全相同。
        messages = [
            *messages,
            Message("assistant", reply or "OK"),
            Message("user", "Reply only OK again."),
        ]
    first, second = usages
    strict = first.cache_write > 0 and second.cache_read > 0
    if strict:
        status, exit_code = "write_read_verified", 0
    elif second.cache_read > 0:
        status = (
            "read_verified_write_not_observed"
            if first.cache_write_reported
            else "read_verified_write_unreported"
        )
        exit_code = 2
    else:
        status, exit_code = "cache_not_observed", 1
    output(
        json.dumps(
            {
                "cache_check": {
                    "status": status,
                    "native_ac4_passed": strict,
                    "stable_sha256": hashlib.sha256(stable.encode()).hexdigest(),
                }
            },
            ensure_ascii=False,
        )
    )
    return exit_code


async def run(args: argparse.Namespace) -> int:
    configs = load(args.config).providers
    cfg = (
        next((c for c in configs if c.name == args.provider), None)
        if args.provider
        else configs[0]
    )
    if cfg is None:
        raise ValueError("未找到指定供应商")
    check = getattr(args, "check_cache", False)
    provider = new_provider(replace(cfg, thinking=False) if check else cfg)
    conv = Conversation()
    agent = Agent(provider, new_default_registry(), __version__)
    mode = Mode.PLAN if args.plan else Mode.NORMAL

    def output(text: str) -> None:
        print(text.replace(cfg.api_key, "[已隐藏密钥]"), flush=True)

    try:
        if check:
            return await check_cache(provider, output)
        for text in args.messages or [
            "你好，请简短介绍自己。",
            "请再简短说明你的工具能力。",
        ]:
            conv.add_user(text)
            reply: list[str] = []
            async with aclosing(agent.run(conv, mode)) as stream:
                async for event in stream:
                    if event.usage:
                        output(
                            json.dumps(
                                {"usage": asdict(event.usage)}, ensure_ascii=False
                            )
                        )
                    if event.text:
                        # 合并后再脱敏，避免密钥跨多个流式片段时漏替换。
                        reply.append(event.text)
                    if event.tool:
                        output(f"tool {event.tool.name}: {event.tool.phase.value}")
                    if event.notice:
                        output(event.notice)
                    if event.err:
                        raise RuntimeError("模型请求失败，请检查配置和服务状态")
            if reply:
                output("".join(reply))
        return 0
    finally:
        await provider.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(".termcode/config.yaml"))
    parser.add_argument("--provider", help="配置中的供应商名称")
    parser.add_argument("--plan", action="store_true", help="只读规划模式")
    parser.add_argument(
        "--check-cache",
        action="store_true",
        help="仅用人工测试文本检查冷缓存；退出码 0=写读均验证，2=只验证读取，1=未命中",
    )
    parser.add_argument("messages", nargs="*")
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
