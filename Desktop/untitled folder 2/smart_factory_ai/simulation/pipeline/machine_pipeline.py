from simulation.utils.clock import SimulationClock

from simulation.events.board_event import BoardEvent


class MachinePipeline:

    def process(

        self,

        machine,

        station,

        board

    ):

        machine.process_board()

        event = BoardEvent(

            board_id=board,

            machine=machine.name,

            station=station.name,

            status="PASS",

            timestamp=SimulationClock.now()

        )

        return event