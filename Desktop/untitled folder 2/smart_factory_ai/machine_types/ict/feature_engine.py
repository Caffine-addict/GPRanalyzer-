from statistics import mean
from statistics import median
from statistics import stdev
from statistics import variance

from machine_types.ict.models import (
    NormalizedBoard,
    ICTFeatures,
)


class ICTFeatureEngine:

    def build(self, board: NormalizedBoard) -> ICTFeatures:

        measurements = []
        margins = []

        measured_tests = 0
        missing_measurements = 0

        duplicate_tests = (
            len(board.tests)
            - len({t.test_name for t in board.tests})
        )

        for test in board.tests:

            if test.measured_value is None:
                missing_measurements += 1
            else:
                measured_tests += 1
                measurements.append(test.measured_value)

            if test.margin is not None:
                margins.append(test.margin)

        # ----------------------------
        # Measurement Statistics
        # ----------------------------

        if len(measurements) == 0:

            avg_measurement = 0
            median_measurement = 0
            std_measurement = 0
            variance_measurement = 0
            min_measurement = 0
            max_measurement = 0

        elif len(measurements) == 1:

            avg_measurement = measurements[0]
            median_measurement = measurements[0]
            std_measurement = 0
            variance_measurement = 0
            min_measurement = measurements[0]
            max_measurement = measurements[0]

        else:

            avg_measurement = mean(measurements)
            median_measurement = median(measurements)
            std_measurement = stdev(measurements)
            variance_measurement = variance(measurements)
            min_measurement = min(measurements)
            max_measurement = max(measurements)

        measurement_range = (
            max_measurement - min_measurement
            if measured_tests > 0
            else 0
        )

        # ----------------------------
        # Margin Statistics
        # ----------------------------

        if len(margins) == 0:

            avg_margin = 0
            median_margin = 0
            std_margin = 0
            min_margin = 0
            max_margin = 0

        elif len(margins) == 1:

            avg_margin = margins[0]
            median_margin = margins[0]
            std_margin = 0
            min_margin = margins[0]
            max_margin = margins[0]

        else:

            avg_margin = mean(margins)
            median_margin = median(margins)
            std_margin = stdev(margins)
            min_margin = min(margins)
            max_margin = max(margins)

        # ----------------------------
        # Pass / Fail
        # ----------------------------

        pass_rate = (
            board.passed_tests / board.total_tests * 100
            if board.total_tests
            else 0
        )

        fail_rate = 100 - pass_rate

        return ICTFeatures(

            total_tests=board.total_tests,

            passed_tests=board.passed_tests,

            failed_tests=board.failed_tests,

            pass_rate=pass_rate,

            fail_rate=fail_rate,

            avg_measurement=avg_measurement,

            median_measurement=median_measurement,

            std_measurement=std_measurement,

            variance_measurement=variance_measurement,

            min_measurement=min_measurement,

            max_measurement=max_measurement,

            measurement_range=measurement_range,

            avg_margin=avg_margin,

            median_margin=median_margin,

            std_margin=std_margin,

            min_margin=min_margin,

            max_margin=max_margin,

            measured_tests=measured_tests,

            missing_measurements=missing_measurements,

            duplicate_tests=duplicate_tests,

            unique_tests=board.total_tests - duplicate_tests

        )