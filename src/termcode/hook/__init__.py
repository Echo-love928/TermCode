"""生命周期 Hook 的加载与分派接口。"""

from termcode.hook.engine import DispatchResult, Engine
from termcode.hook.event import Event
from termcode.hook.loader import load

__all__ = ["DispatchResult", "Engine", "Event", "load"]
