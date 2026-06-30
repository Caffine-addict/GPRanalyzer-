from dataclasses import dataclass

from simulation.models.machine import Machine


@dataclass
class Station:

    name: str

    machine: Machine