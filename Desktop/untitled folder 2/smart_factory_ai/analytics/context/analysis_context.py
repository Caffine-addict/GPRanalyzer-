from dataclasses import dataclass

from analytics.health.health_engine import HealthEngine
from analytics.risk.risk_engine import RiskEngine
@dataclass
class AnalysisContext:

    event: object

    baseline: dict | None = None

    quality: dict | None = None

    drift: dict | None = None

    health: dict | None = None

    risk: dict | None = None

    recommendation: dict | None = None

    alerts: list | None = None