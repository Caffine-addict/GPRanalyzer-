from analytics.common.statistics import Statistics

from analytics.common.confidence import (

    ConfidenceCalculator

)


values = [

    10,

    12,

    15,

    18,

    20,

    25

]

print()

print(

    "Mean:",

    Statistics.mean(values)

)

print(

    "Median:",

    Statistics.median(values)

)

print(

    "Variance:",

    Statistics.variance(values)

)

print(

    "Std:",

    Statistics.std(values)

)

print(

    "Range:",

    Statistics.value_range(values)

)

print(

    "IQR:",

    Statistics.iqr(values)

)

print(

    "CV:",

    Statistics.coefficient_of_variation(values)

)

print(

    "Moving Average:",

    Statistics.moving_average(

        values,

        3

    )

)

confidence = (

    ConfidenceCalculator.calculate(

        sample_size=500,

        coefficient_of_variation=0.12

    )

)

print()

print(

    "Confidence:",

    confidence

)