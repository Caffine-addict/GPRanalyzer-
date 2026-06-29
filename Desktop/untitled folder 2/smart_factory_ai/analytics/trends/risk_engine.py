class RiskEngine:

    @staticmethod
    def calculate_risk(
        health_score,
        anomaly_score=0
    ):

        risk = 100 - health_score

        risk += abs(anomaly_score) * 20

        return round(
            min(risk, 100),
            2
        )