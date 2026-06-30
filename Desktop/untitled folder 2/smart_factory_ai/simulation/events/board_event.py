from dataclasses import dataclass
from datetime import datetime


@dataclass
class BoardEvent:

    board_id: str

    machine: str

    station: str

    status: str

    timestamp: datetime