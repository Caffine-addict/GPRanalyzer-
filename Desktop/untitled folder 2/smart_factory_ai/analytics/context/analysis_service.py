from analytics.context.analysis_context import AnalysisContext

from analytics.baseline.baseline_engine import BaselineEngine

from analytics.quality.quality_engine import QualityEngine

from analytics.drift.drift_engine import DriftEngine

from analytics.health.health_engine import HealthEngine

from analytics.risk.risk_engine import RiskEngine

class AnalysisService:

    def __init__(self):
        self.health_engine = HealthEngine()
        self.risk_engine = RiskEngine()

        self.baseline_engine = BaselineEngine()

        self.quality_engine = QualityEngine()

        self.drift_engine = DriftEngine()

    def build(self, event):

        context = AnalysisContext(

            event=event

        )

        context.baseline = self.baseline_engine.build(

            event.board_family

        )

        context.quality = self.quality_engine.evaluate(

            event

        )

        context.drift = self.drift_engine.evaluate(

            event

        )
        context.health = self.health_engine.evaluate(
            context
        )

        context.risk = self.risk_engine.evaluate(
            context
        )
        return context