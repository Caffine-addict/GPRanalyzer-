class HealthScoreCalculator:

    @staticmethod
    def calculate(
        temperature,
        vibration,
        current,
        rpm
    ):

        score = 100

        # Temperature penalty

        if temperature > 70:
            score -= (temperature - 70) * 0.8

        # Vibration penalty

        if vibration > 1.0:
            score -= (vibration - 1.0) * 20

        # Current penalty

        if current > 5:
            score -= (current - 5) * 5

        # RPM penalty

        if rpm < 1300:
            score -= 5

        return max(round(score, 2), 0)