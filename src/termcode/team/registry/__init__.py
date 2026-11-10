"""后台子 Agent 和团队队员共用的名称索引。"""

from threading import Lock


class AgentNameRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self.by_name: dict[str, str] = {}
        self.by_id: dict[str, str] = {}

    def register(self, name: str, agent_id: str) -> None:
        with self._lock:
            if previous := self.by_name.get(name):
                self.by_id.pop(previous, None)
            if old_name := self.by_id.get(agent_id):
                self.by_name.pop(old_name, None)
            self.by_name[name] = agent_id
            self.by_id[agent_id] = name

    def unregister(self, name: str, agent_id: str | None = None) -> None:
        with self._lock:
            current = self.by_name.get(name)
            if current and (agent_id is None or current == agent_id):
                del self.by_name[name]
                del self.by_id[current]

    def resolve(self, name_or_id: str) -> str | None:
        with self._lock:
            return self.by_name.get(name_or_id) or (
                name_or_id if name_or_id in self.by_id else None
            )

    def name_of(self, agent_id: str) -> str | None:
        with self._lock:
            return self.by_id.get(agent_id)
