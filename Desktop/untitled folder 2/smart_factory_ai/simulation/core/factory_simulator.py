from simulation.core.factory_loader import FactoryLoader


class FactorySimulator:

    def __init__(self):

        self.factory = FactoryLoader.load(
            "simulation/config/default_factory.json"
        )

    def start(self):

        print()

        print("=" * 60)

        print(self.factory.name)

        print("=" * 60)

        for line in self.factory.lines:

            print()

            print(line.name)

            for station in line.stations:

                station.machine.start()

                print(
                    f"{station.machine.name:<10}"
                    f"{station.machine.status}"
                )

        print()

        print("Factory Simulation Started")