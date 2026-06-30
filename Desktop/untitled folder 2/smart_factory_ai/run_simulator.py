from simulation.core.factory_simulator import FactorySimulator

from simulation.core.simulation_engine import SimulationEngine


factory = FactorySimulator()

factory.start()

engine = SimulationEngine()

print()

print("=" * 60)

print("LIVE PRODUCTION")

print("=" * 60)

for _ in range(5):

    engine.produce()