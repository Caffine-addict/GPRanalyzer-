from simulation.bus.event_bus import EventBus

from simulation.generators.production_generator import ProductionGenerator

from simulation.subscribers.logger import LoggerSubscriber


class SimulationEngine:

    def __init__(self):

        self.production = ProductionGenerator()

        self.bus = EventBus()

        self.bus.subscribe(

            "board_created",

            LoggerSubscriber()

        )

    def produce(self):

        board = self.production.next_board()

        self.bus.publish(

            "board_created",

            {

                "event": "board_created",

                "board": board

            }

        )

        return board