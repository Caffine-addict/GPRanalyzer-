from factory.registry.analytics_registry import AnalyticsRegistry

from analytics.baseline.baseline_engine import BaselineEngine
from analytics.quality.quality_engine import QualityEngine
from analytics.health.health_engine import HealthEngine
from analytics.drift.drift_engine import DriftEngine
from analytics.risk.risk_engine import RiskEngine
from analytics.recommendation.recommendation_engine import RecommendationEngine
from analytics.alerts.alert_engine import AlertEngine


class AnalysisOrchestrator:
    """
    Central coordinator for all analytics engines.

    Every machine analysis passes through this orchestrator.
    """

    def __init__(self):

        self.registry = AnalyticsRegistry()

        self._register_default_engines()

    def _register_default_engines(self):

        self.registry.register(
            "baseline",
            BaselineEngine()
        )

        self.registry.register(
            "quality",
            QualityEngine()
        )

        self.registry.register(
            "health",
            HealthEngine()
        )

        self.registry.register(
            "drift",
            DriftEngine()
        )

        self.registry.register(
            "risk",
            RiskEngine()
        )

        self.registry.register(
            "recommendation",
            RecommendationEngine()
        )

        self.registry.register(
            "alert",
            AlertEngine()
        )

    def get_engine(self, name):

        return self.registry.get(name)

    def get_registered_engines(self):

        return self.registry.names()

    def analyze(self, engine_name, *args, **kwargs):

        engine = self.registry.get(engine_name)

        if engine is None:

            raise ValueError(
                f"Unknown analytics engine: {engine_name}"
            )

        return engine.analyze(*args, **kwargs)

    def analyze_all(self, input_map):

        """
        input_map:

        {
            "baseline": board_family,
            "quality": feature_vector,
            "health": feature_vector,
            ...
        }
        """

        results = {}

        for name in self.registry.names():

            if name not in input_map:
                continue

            results[name] = self.registry.get(
                name
            ).analyze(
                input_map[name]
            )

        return results