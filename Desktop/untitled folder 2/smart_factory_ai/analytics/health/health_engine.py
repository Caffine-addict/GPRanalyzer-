class HealthEngine:

    def evaluate(self, context):

        quality = context.quality
        drift = context.drift

        score = quality["score"]

        sigma = max(
            drift["metrics"]["measurement_sigma"],
            drift["metrics"]["margin_sigma"]
        )

        if sigma >= 3:
            score -= 25

        elif sigma >= 2:
            score -= 10

        score = max(0, min(score, 100))

        if score >= 90:
            health = "EXCELLENT"

        elif score >= 75:
            health = "GOOD"

        elif score >= 60:
            health = "FAIR"

        elif score >= 40:
            health = "POOR"

        else:
            health = "CRITICAL"

        context.health = {

            "score": score,

            "health": health

        }

        return context.health