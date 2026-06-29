from dataclasses import dataclass
from datetime import datetime


@dataclass
class FeatureVector:

    machine_type: str

    board_name: str

    board_family: str

    program_name: str

    timestamp: datetime

    features: dict