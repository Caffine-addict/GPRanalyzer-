from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# =====================================================
# RAW ICT PARSER MODELS
# =====================================================

@dataclass
class ICTTest:

    sequence: int

    test_name: str

    test_id: Optional[int]

    retry: Optional[int]

    description: str

    status: str

    measured_value: Optional[float]

    low_limit: Optional[float]

    high_limit: Optional[float]

    unit: Optional[str]

    test_points: Optional[str]

    execution_order: Optional[int]

@dataclass
class ICTBoard:

    board_name: str

    product_name: str

    revision: str

    product_number: str

    board_family: str

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

# =====================================================
# NORMALIZED MODELS
# =====================================================

@dataclass
class NormalizedTest:

    test_id: str

    test_name: str

    result: str

    measured_value: Optional[float]

    low_limit: Optional[float]

    high_limit: Optional[float]

    unit: Optional[str]

    margin: Optional[float]


@dataclass
class NormalizedBoard:

    board_name: str

    board_family: str

    program_name: str

    timestamp: datetime

    passed_tests: int

    failed_tests: int

    total_tests: int

    tests: list[NormalizedTest]


# =====================================================
# FEATURE MODEL
# =====================================================

@dataclass
class ICTFeatures:

    total_tests: int

    passed_tests: int

    failed_tests: int

    pass_rate: float

    fail_rate: float

    avg_measurement: float

    median_measurement: float

    std_measurement: float

    variance_measurement: float

    min_measurement: float

    max_measurement: float

    measurement_range: float

    avg_margin: float

    median_margin: float

    std_margin: float

    min_margin: float

    max_margin: float

    measured_tests: int

    missing_measurements: int

    duplicate_tests: int

    unique_tests: int


# =====================================================
# FINAL EVENT
# =====================================================

@dataclass
class BoardEvent:

    board_name: str

    board_family: str

    program_name: str

    timestamp: datetime

    total_tests: int

    passed_tests: int

    failed_tests: int

    pass_rate: float

    fail_rate: float

    avg_measurement: float

    median_measurement: float

    std_measurement: float

    variance_measurement: float

    min_measurement: float

    max_measurement: float

    measurement_range: float

    avg_margin: float

    median_margin: float

    std_margin: float

    min_margin: float

    max_margin: float

    measured_tests: int

    missing_measurements: int

    duplicate_tests: int

    unique_tests: int

    metadata: dict
