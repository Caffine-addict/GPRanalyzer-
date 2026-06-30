from typing import Dict

from analytics.machine.common.base_engine import BaseAnalyticsEngine


class AnalyticsRegistry:

    def __init__(self):
        self._engines: Dict[str, BaseAnalyticsEngine] = {}

    def register(self, name: str, engine: BaseAnalyticsEngine):
        self._engines[name] = engine

    def get(self, name: str):
        return self._engines.get(name)

    def all(self):
        return self._engines.values()

    def names(self):
        return list(self._engines.keys())