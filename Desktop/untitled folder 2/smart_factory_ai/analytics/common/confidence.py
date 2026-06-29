class ConfidenceCalculator:

    @staticmethod
    def calculate(

        sample_size,

        coefficient_of_variation

    ):

        sample_score = min(
            sample_size / 1000,
            1.0
        )

        variance_score = max(
            0,
            1 - coefficient_of_variation
        )

        confidence = (

            sample_score * 0.6

            +

            variance_score * 0.4

        )

        return round(

            confidence * 100,

            2

        )
    