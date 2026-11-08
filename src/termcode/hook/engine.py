"""按加载顺序分派事件，跟踪仅执行一次的规则及后台任务。"""

import asyncio
import sys
from dataclasses import dataclass, field

from termcode.hook.event import Event, is_blocking
from termcode.hook.executor import ExecutionResult, Executor
from termcode.hook.matcher import eval_condition
from termcode.hook.rule import Payload, Rule


@dataclass
class DispatchResult:
    blocked: bool = False
    reason: str = ""
    blocking_hook_name: str = ""
    injected_prompts: list[str] = field(default_factory=list)

    def block_message(self) -> str:
        return f"[hook {self.blocking_hook_name}] {self.reason}"


class Engine:
    def __init__(self, rules: list[Rule], sources: list[str]) -> None:
        self._rules = tuple(rules)
        self._sources = tuple(sources)
        self._once_fired: set[str] = set()
        # Textual 运行期间会替换 sys.stderr，保留启动时的日志流。
        self._stderr = sys.stderr
        self._executor = Executor()
        self._tasks: set[asyncio.Task[ExecutionResult]] = set()

    @property
    def rules(self) -> list[Rule]:
        return list(self._rules)

    @property
    def sources(self) -> list[str]:
        return list(self._sources)

    async def _execute(
        self, rule: Rule, payload: Payload, *, blocking: bool
    ) -> ExecutionResult:
        outcome = await self._executor.run(rule, payload, blocking=blocking)
        if outcome.err is not None:
            reason = (
                " ".join(str(outcome.err).splitlines()) or type(outcome.err).__name__
            )
            print(
                f"[hook {rule.name}] {rule.event} failed: {reason}", file=self._stderr
            )
        return outcome

    async def dispatch(
        self, event: Event, payload: Payload, *, once_fired: set[str] | None = None
    ) -> DispatchResult:
        result = DispatchResult()
        payload = {**payload, "event": event.value}
        fired = self._once_fired if once_fired is None else once_fired
        blocking = is_blocking(event)
        for rule in self._rules:
            if rule.event is not event or (rule.only_once and rule.name in fired):
                continue
            if not eval_condition(rule.condition, payload):
                continue
            # 先占用执行名额，防止并发分派重复运行同一条规则。
            if rule.only_once:
                fired.add(rule.name)
            if rule.asyncio_mode:
                task = asyncio.create_task(self._execute(rule, payload, blocking=False))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
                continue
            succeeded = False
            try:
                outcome = await self._execute(rule, payload, blocking=blocking)
                succeeded = outcome.err is None
            finally:
                if rule.only_once and not succeeded:
                    fired.discard(rule.name)
            if not succeeded:
                continue
            if outcome.prompt:
                result.injected_prompts.append(outcome.prompt)
            if blocking and outcome.blocked:
                result.blocked = True
                result.reason = outcome.reason
                result.blocking_hook_name = rule.name
                break
        return result

    async def reset_for_new_session(self) -> None:
        self._once_fired = set()

    async def aclose(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._executor.aclose()
