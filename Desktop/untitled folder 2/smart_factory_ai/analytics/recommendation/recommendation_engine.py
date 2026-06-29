class RecommendationEngine:

    def evaluate(self, context):

        quality = context.quality
        drift = context.drift
        health = context.health
        risk = context.risk

        recommendations = []

        priority = "LOW"

        # ------------------------
        # Quality
        # ------------------------

        if quality["status"] != "HEALTHY":

            recommendations.append(
                "Review duplicate ICT test definitions."
            )

            recommendations.append(
                "Check average test margin."
            )

        # ------------------------
        # Drift
        # ------------------------

        if drift["drift"]:

            recommendations.append(
                "Inspect fixture calibration."
            )

            recommendations.append(
                "Review measurement process drift."
            )

        # ------------------------
        # Health
        # ------------------------

        if health["score"] < 70:

            recommendations.append(
                "Schedule maintenance inspection."
            )

        # ------------------------
        # Risk
        # ------------------------

        if risk["risk_level"] == "CRITICAL":

            priority = "HIGH"

        elif risk["risk_level"] == "HIGH":

            priority = "MEDIUM"

        context.recommendation = {

            "priority": priority,

            "recommendations": recommendations

        }

        return context.recommendation