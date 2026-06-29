from dataclasses import dataclass
from datetime import datetime


@dataclass
class EngineResult:

    engine: str

    value: object

    confidence: float

    warnings: list

    recommendations: list

    generated_at: datetime