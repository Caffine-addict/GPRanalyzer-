from dataclasses import dataclass
from typing import List
from typing import Dict
@dataclass
class NormalizedTest:

    test_id: str

    test_name: str

    result: str

    measured_value: float | None

    low_limit: float |None

    high_limit: float | None

    unit: str | None

    margin: float | None


@dataclass
class NormalizedBoard:

    board_name: str

    board_family: str

    program_name: str

    timestamp: str

    passed_tests: int

    failed_tests: int

    total_tests: int

    tests: List[NormalizedTest]



@dataclass

class BoardEvent:

    board_name: str

    board_family: str

    program_name: str

    timestamp: str

    # -------------------------

    # Quality

    # -------------------------

    total_tests: int

    passed_tests: int

    failed_tests: int

    pass_rate: float

    fail_rate: float

    # -------------------------

    # Measurement Statistics

    # -------------------------

    avg_measurement: float

    median_measurement: float

    std_measurement: float

    variance_measurement: float

    min_measurement: float

    max_measurement: float

    measurement_range: float

    # -------------------------

    # Margin Statistics

    # -------------------------

    avg_margin: float

    median_margin: float

    std_margin: float

    min_margin: float

    max_margin: float

    # -------------------------

    # Metadata

    # -------------------------

    metadata: Dict
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

    min_measurement: float

    max_measurement: float

    avg_margin: float

    min_margin: float

    max_margin: float

    coverage: float

    test_density: float

    duplicate_tests: int

@dataclass
class ICTFeatures:

    # ------------------------
    # Quality
    # ------------------------

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