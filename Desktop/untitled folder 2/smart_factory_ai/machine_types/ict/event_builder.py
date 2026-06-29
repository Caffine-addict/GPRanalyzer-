from machine_types.ict.models import (
    NormalizedBoard,
    ICTFeatures,
    BoardEvent,
)


class ICTEventBuilder:

    def build(
        self,
        board: NormalizedBoard,
        features: ICTFeatures
    ) -> BoardEvent:

        return BoardEvent(

            board_name=board.board_name,

            board_family=board.board_family,

            program_name=board.program_name,

            timestamp=board.timestamp,

            total_tests=features.total_tests,

            passed_tests=features.passed_tests,

            failed_tests=features.failed_tests,

            pass_rate=features.pass_rate,

            fail_rate=features.fail_rate,

            avg_measurement=features.avg_measurement,

            median_measurement=features.median_measurement,

            std_measurement=features.std_measurement,

            variance_measurement=features.variance_measurement,

            min_measurement=features.min_measurement,

            max_measurement=features.max_measurement,

            measurement_range=features.measurement_range,

            avg_margin=features.avg_margin,

            median_margin=features.median_margin,

            std_margin=features.std_margin,

            min_margin=features.min_margin,

            max_margin=features.max_margin,

            measured_tests=features.measured_tests,

            missing_measurements=features.missing_measurements,

            duplicate_tests=features.duplicate_tests,

            unique_tests=features.unique_tests,

            metadata={

                "machine_type": "ICT",

                "version": 1,

                "parser": "ICTParser",

                "feature_engine": "ICTFeatureEngine"

            }

        )