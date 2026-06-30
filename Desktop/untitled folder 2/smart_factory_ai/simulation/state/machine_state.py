from enum import Enum


class MachineState(Enum):

    READY = "READY"

    RUNNING = "RUNNING"

    IDLE = "IDLE"

    BLOCKED = "BLOCKED"

    DOWN = "DOWN"

    MAINTENANCE = "MAINTENANCE"