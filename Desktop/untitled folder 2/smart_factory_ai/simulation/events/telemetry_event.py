from dataclasses import dataclass
from datetime import datetime


@dataclass
class TelemetryEvent:

    machine: str

    health: float

    throughput: int

    defect_rate: float

    timestamp: datetime