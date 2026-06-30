from abc import ABC, abstractmethod


class AnalyticsInterface(ABC):

    @abstractmethod
    def analyze(self, *args, **kwargs):
        pass