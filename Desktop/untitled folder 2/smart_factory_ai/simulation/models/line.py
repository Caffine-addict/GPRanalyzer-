from dataclasses import dataclass, field

from simulation.models.station import Station


@dataclass
class Line:

    name: str

    stations: list[Station] = field(default_factory=list)

    def add_station(self, station: Station):

        self.stations.append(station)