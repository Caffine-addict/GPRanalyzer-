import json

from simulation.models.factory import Factory
from simulation.models.line import Line
from simulation.models.station import Station
from simulation.models.machine import Machine


class FactoryLoader:

    @staticmethod
    def load(path):

        with open(path, "r") as f:

            config = json.load(f)

        factory = Factory(config["factory_name"])

        for line_data in config["lines"]:

            line = Line(line_data["name"])

            for station_data in line_data["stations"]:

                machine = Machine(
                    name=station_data["machine"]
                )

                station = Station(
                    name=station_data["name"],
                    machine=machine
                )

                line.add_station(station)

            factory.add_line(line)

        return factory