from statistics import mean
from statistics import median
from statistics import pstdev

from analytics.baseline.baseline_repository import (
    BaselineRepository,
)


class BaselineEngine:

    def __init__(self):

        self.repo = BaselineRepository()

    def build(
        self,
        board_family
    ):

        events = (
            self.repo.by_board_family(
                board_family
            )
        )

        if len(events) == 0:

            return None

        pass_rates = [
            e.pass_rate
            for e in events
        ]

        margins = [
            e.avg_margin
            for e in events
        ]

        measurements = [
            e.avg_measurement
            for e in events
        ]

        return {

            "board_family": board_family,

            "boards": len(events),

            "pass_rate_mean": mean(pass_rates),

            "pass_rate_std": (
                pstdev(pass_rates)
                if len(pass_rates) > 1
                else 0
            ),

            "measurement_mean": mean(measurements),

            "measurement_std": (
                pstdev(measurements)
                if len(measurements) > 1
                else 0
            ),

            "margin_mean": mean(margins),

            "margin_std": (
                pstdev(margins)
                if len(margins) > 1
                else 0
            ),

            "median_margin": median(margins)

        }