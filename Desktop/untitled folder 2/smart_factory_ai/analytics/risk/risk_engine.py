class RiskEngine:

    def evaluate(self, context):

        health = context.health
        quality = context.quality
        drift = context.drift

        score = health["score"]

        risk = 0
        reasons = []

        if score < 90:

            risk += 20
            reasons.append("Health score reduced")

        if score < 70:

            risk += 20
            reasons.append("Poor health")

        if score < 50:

            risk += 30
            reasons.append("Critical health")

        if drift["drift"]:

            risk += 25
            reasons.append("Process drift detected")

        if quality["status"] != "HEALTHY":

            risk += 15
            reasons.append("Quality degradation")

        risk = min(risk, 100)

        if risk < 20:

            level = "LOW"

        elif risk < 50:

            level = "MEDIUM"

        elif risk < 75:

            level = "HIGH"

        else:

            level = "CRITICAL"

        context.risk = {

            "risk_score": risk,

            "risk_level": level,

            "health_score": score,

            "reasons": reasons

        }

        return context.risk