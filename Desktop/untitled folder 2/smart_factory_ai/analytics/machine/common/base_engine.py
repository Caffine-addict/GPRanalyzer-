from abc import ABC, abstractmethod
from typing import Any


class BaseAnalyticsEngine(ABC):
    """
    Base class for every analytics engine.
    """

    def __init__(self):
        self.engine_name = self.__class__.__name__

    @abstractmethod
    def analyze(self, *args, **kwargs) -> Any:
        """
        Execute the engine.
        """
        pass

    def info(self) -> str:
        return self.engine_name