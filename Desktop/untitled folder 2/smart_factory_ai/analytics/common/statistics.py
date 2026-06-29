import statistics


class Statistics:

    @staticmethod
    def mean(values):

        if not values:
            return 0

        return statistics.mean(values)

    @staticmethod
    def median(values):

        if not values:
            return 0

        return statistics.median(values)

    @staticmethod
    def variance(values):

        if len(values) < 2:
            return 0

        return statistics.pvariance(values)

    @staticmethod
    def std(values):

        if len(values) < 2:
            return 0

        return statistics.pstdev(values)

    @staticmethod
    def minimum(values):

        if not values:
            return 0

        return min(values)

    @staticmethod
    def maximum(values):

        if not values:
            return 0

        return max(values)

    @staticmethod
    def value_range(values):

        if not values:
            return 0

        return max(values) - min(values)

    @staticmethod
    def percentile(values, percentile):

        if not values:
            return 0

        values = sorted(values)

        k = (len(values) - 1) * percentile

        f = int(k)

        c = min(f + 1, len(values) - 1)

        if f == c:
            return values[f]

        d = k - f

        return values[f] * (1 - d) + values[c] * d

    @staticmethod
    def iqr(values):

        if len(values) < 4:
            return 0

        q1 = Statistics.percentile(values, 0.25)

        q3 = Statistics.percentile(values, 0.75)

        return q3 - q1

    @staticmethod
    def coefficient_of_variation(values):

        mean = Statistics.mean(values)

        if mean == 0:
            return 0

        return Statistics.std(values) / mean

    @staticmethod
    def moving_average(values, window):

        if len(values) < window:
            return []

        averages = []

        for i in range(len(values) - window + 1):

            averages.append(

                Statistics.mean(

                    values[i:i + window]

                )

            )

        return averages