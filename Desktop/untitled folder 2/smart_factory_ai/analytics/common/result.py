from dataclasses import dataclass
from datetime import datetime
from typing import Any

@dataclass
class AnalysisResult:
    engine: str
    success: bool
    data: Any
    message: str = ""

@dataclass
class EngineResult:

    engine: str

    value: object

    confidence: float

    warnings: list

    recommendations: list

    generated_at: datetime
