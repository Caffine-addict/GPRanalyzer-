from simulation.generators.production_generator import ProductionGenerator


class SimulationEngine:

    def __init__(self):

        self.production = ProductionGenerator()

    def produce(self):

        return self.production.next_board()