from dataclasses import dataclass

from simulation.state.machine_state import MachineState


@dataclass
class Machine:

    name: str

    state: MachineState = MachineState.READY

    health: float = 100.0

    throughput: int = 0

    defect_rate: float = 0.0

    processed_boards: int = 0

    def start(self):

        self.state = MachineState.RUNNING

    def stop(self):

        self.state = MachineState.IDLE

    def process_board(self):

        self.processed_boards += 1

        self.throughput += 1