from simulation.core.factory_simulator import FactorySimulator
from simulation.core.simulation_engine import SimulationEngine

factory = FactorySimulator()
factory.start()

engine = SimulationEngine()

print()

print("Starting Production")

print("-" * 40)

for _ in range(10):

    board = engine.produce()

    print(board)