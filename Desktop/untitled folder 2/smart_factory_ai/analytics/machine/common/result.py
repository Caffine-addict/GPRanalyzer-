from dataclasses import dataclass
from typing import Any


@dataclass
class AnalysisResult:
    engine: str
    success: bool
    result: Any