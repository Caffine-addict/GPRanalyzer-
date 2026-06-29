from analytics.baseline.baseline_engine import BaselineEngine


class QualityEngine:

    def __init__(self):

        self.baseline_engine = BaselineEngine()

    def evaluate(self, event):

        baseline = self.baseline_engine.build(
            event.board_family
        )

        if baseline is None:

            return {

                "status": "UNKNOWN",

                "score": 0,

                "reason": "No baseline available"

            }

        score = 100

        reasons = []

        # ----------------------------
        # Pass Rate
        # ----------------------------

        if event.pass_rate < baseline["pass_rate_mean"]:

            score -= 40

            reasons.append(
                "Pass rate below baseline"
            )

        # ----------------------------
        # Margin
        # ----------------------------

        if event.avg_margin < baseline["margin_mean"]:

            score -= 20

            reasons.append(
                "Average margin below baseline"
            )

        # ----------------------------
        # Missing Measurements
        # ----------------------------

        if event.missing_measurements > 0:

            score -= 10

            reasons.append(
                "Missing measurements detected"
            )

        # ----------------------------
        # Duplicate Tests
        # ----------------------------

        if event.duplicate_tests > 0:

            score -= 5

            reasons.append(
                "Duplicate tests present"
            )

        score = max(score, 0)

        if score >= 90:

            status = "HEALTHY"

        elif score >= 70:

            status = "WARNING"

        else:

            status = "CRITICAL"

        return {

            "status": status,

            "score": score,

            "reasons": reasons

        }