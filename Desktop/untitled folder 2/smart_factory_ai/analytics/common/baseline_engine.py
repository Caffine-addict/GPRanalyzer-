from abc import ABC, abstractmethod
from typing import Any


class BaseAnalyticsEngine(ABC):
    """
    Base class for all analytics engines.
    Every engine must implement analyze().
    """

    def __init__(self):
        self.engine_name = self.__class__.__name__

    @abstractmethod
    def analyze(self, *args, **kwargs) -> Any:
        pass

    def get_engine_name(self):
        return self.engine_name