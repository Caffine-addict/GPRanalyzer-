from analytics.baseline.baseline_engine import (
    BaselineEngine
)


class DriftEngine:

    def __init__(self):

        self.baseline = BaselineEngine()

    def evaluate(self, event):

        baseline = self.baseline.build(
            event.board_family
        )

        if baseline is None:

            return {

                "drift": False,

                "severity": "UNKNOWN",

                "metrics": {}

            }

        metrics = {}

        # ----------------------------
        # Measurement Drift
        # ----------------------------

        measurement_delta = abs(

            event.avg_measurement
            - baseline["measurement_mean"]

        )

        measurement_std = baseline["measurement_std"]

        if measurement_std == 0:

            measurement_sigma = 0

        else:

            measurement_sigma = (

                measurement_delta
                / measurement_std

            )

        metrics["measurement_sigma"] = measurement_sigma

        # ----------------------------
        # Margin Drift
        # ----------------------------

        margin_delta = abs(

            event.avg_margin
            - baseline["margin_mean"]

        )

        margin_std = baseline["margin_std"]

        if margin_std == 0:

            margin_sigma = 0

        else:

            margin_sigma = (

                margin_delta
                / margin_std

            )

        metrics["margin_sigma"] = margin_sigma

        # ----------------------------
        # Severity
        # ----------------------------

        maximum = max(

            measurement_sigma,

            margin_sigma

        )

        if maximum < 2:

            severity = "NORMAL"

        elif maximum < 3:

            severity = "WARNING"

        else:

            severity = "CRITICAL"

        return {

            "drift": maximum >= 2,

            "severity": severity,

            "metrics": metrics

        }