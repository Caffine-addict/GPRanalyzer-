from simulation.bus.event_bus import EventBus

from simulation.generators.production_generator import ProductionGenerator

from simulation.pipeline.machine_pipeline import MachinePipeline

from simulation.subscribers.logger import LoggerSubscriber

from simulation.core.factory_loader import FactoryLoader

from simulation.subscribers.analytics_subscriber import AnalyticsSubscriber

class SimulationEngine:

    def __init__(self):

        self.bus = EventBus()

        self.production = ProductionGenerator()

        self.pipeline = MachinePipeline()

        self.factory = FactoryLoader.load(
            "simulation/config/default_factory.json"
        )
        self.bus.subscribe(

            "board_processed",
            AnalyticsSubscriber()

        )

        self.bus.subscribe(

            "board_processed",

            LoggerSubscriber()

        )

    def produce(self):

        board = self.production.next_board()

        line = self.factory.lines[0]

        for station in line.stations:

            station.machine.start()

            event = self.pipeline.process(

                station.machine,

                station,

                board

            )

            self.bus.publish(

                "board_processed",

                event

            )