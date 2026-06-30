from dataclasses import dataclass, field

from simulation.models.line import Line


@dataclass
class Factory:

    name: str

    lines: list[Line] = field(default_factory=list)

    def add_line(self, line: Line):

        self.lines.append(line)