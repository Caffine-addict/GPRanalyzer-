from machine_types.ict.models import (
    ICTBoard,
    ICTTest,
    NormalizedBoard,
    NormalizedTest,
)


class ICTNormalizer:

    def normalize(self, board: ICTBoard) -> NormalizedBoard:

        normalized_tests = []

        passed = 0
        failed = 0

        for test in board.tests:

            margin = None

            if (
                test.measured_value is not None
                and test.low_limit is not None
                and test.high_limit is not None
            ):
                margin = min(
                    test.measured_value - test.low_limit,
                    test.high_limit - test.measured_value,
                )

            normalized_tests.append(

                NormalizedTest(

                    test_id=str(test.test_id)
                    if test.test_id is not None
                    else "",

                    test_name=test.test_name,

                    result=test.status,

                    measured_value=test.measured_value,

                    low_limit=test.low_limit,

                    high_limit=test.high_limit,

                    unit=test.unit,

                    margin=margin,

                )

            )

            if test.status == "PASS":
                passed += 1
            else:
                failed += 1

        return NormalizedBoard(

            board_name=board.board_name,

            board_family=board.board_family,

            program_name=board.program_name,

            timestamp=board.timestamp,

            passed_tests=passed,

            failed_tests=failed,

            total_tests=len(normalized_tests),

            tests=normalized_tests,

        )