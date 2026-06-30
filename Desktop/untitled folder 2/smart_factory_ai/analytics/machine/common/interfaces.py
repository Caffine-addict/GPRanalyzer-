from abc import ABC, abstractmethod


class EngineInterface(ABC):

    @abstractmethod
    def analyze(self, *args, **kwargs):
        pass