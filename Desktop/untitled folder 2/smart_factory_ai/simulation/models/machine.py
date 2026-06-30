from dataclasses import dataclass


@dataclass
class Machine:

    name: str

    status: str = "READY"

    health: float = 100.0

    throughput: int = 0

    defect_rate: float = 0.0

    running: bool = False

    def start(self):

        self.running = True

        self.status = "RUNNING"

    def stop(self):

        self.running = False

        self.status = "STOPPED"