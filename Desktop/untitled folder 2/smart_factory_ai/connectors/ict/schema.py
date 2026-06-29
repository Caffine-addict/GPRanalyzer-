from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ICTTest:

    record_type: str

    sequence: int

    test_name: str

    test_id: int | None

    retry: int | None

    description: str

    status: str

    measured_value: float | None

    lower_limit: float | None

    upper_limit: float | None

    unit: str | None

    test_points: str | None

    execution_order: int | None


@dataclass
class ICTBoard:

    board_name: str

    product_name: str

    revision: str

    product_number: str

    program_name: str

    timestamp: datetime

    tests: list[ICTTest] = field(default_factory=list)

    @property
    def total_tests(self):

        return len(self.tests)

    @property
    def passed_tests(self):

        return sum(
            1
            for test in self.tests
            if test.status == "PASS"
        )

    @property
    def failed_tests(self):

        return sum(
            1
            for test in self.tests
            if test.status != "PASS"
        )