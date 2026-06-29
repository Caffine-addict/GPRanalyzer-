from dataclasses import dataclass
from datetime import datetime


@dataclass
class MetricBaseline:

    metric_name: str

    sample_size: int

    mean: float

    median: float

    std: float

    variance: float

    minimum: float

    maximum: float

    value_range: float

    iqr: float

    coefficient_of_variation: float

    percentile_5: float

    percentile_25: float

    percentile_75: float

    percentile_95: float


@dataclass
class ProcessBaseline:

    board_family: str

    program_name: str

    metrics: dict[str, MetricBaseline]

    confidence: float

    generated_at: datetime


@dataclass
class BaselineResult:

    baseline: ProcessBaseline

    confidence: float

    warnings: list[str]

    recommendations: list[str]