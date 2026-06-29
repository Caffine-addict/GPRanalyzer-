from sqlalchemy import (
    Column,
    Integer,
    Float,
    String,
    DateTime,
)
from sqlalchemy.orm import declarative_base
from datetime import datetime
from sqlalchemy import JSON

Base = declarative_base()


class Machine(Base):
    __tablename__ = "machines"

    id = Column(Integer, primary_key=True)

    machine_id = Column(String, unique=True)
    machine_name = Column(String)
    machine_type = Column(String)

    installation_year = Column(Integer)


class Telemetry(Base):
    __tablename__ = "telemetry"

    id = Column(Integer, primary_key=True)

    timestamp = Column(
        DateTime,
        default=datetime.utcnow
    )

    machine_id = Column(String)

    temperature = Column(Float)
    vibration = Column(Float)
    current = Column(Float)
    rpm = Column(Float)

    status = Column(String)
class MachineFeature(Base):
    __tablename__ = "machine_features"

    id = Column(Integer, primary_key=True)

    timestamp = Column(DateTime)

    machine_id = Column(String)

    avg_temperature = Column(Float)

    avg_vibration = Column(Float)

    avg_current = Column(Float)

    avg_rpm = Column(Float)

    health_score = Column(Float)

    anomaly_risk = Column(Float)
class AnomalyEvent(Base):
    __tablename__ = "anomaly_events"
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime)
    machine_id = Column(String)
    anomaly_score = Column(Float)
    prediction = Column(Integer)
class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True)

    timestamp = Column(DateTime)

    machine_id = Column(String)

    severity = Column(String)

    alert_type = Column(String)

    message = Column(String)

    status = Column(String, default="OPEN")
class ICTTestResult(Base):

    __tablename__ = "ict_test_results"

    id = Column(Integer, primary_key=True)

    file_name = Column(String)

    board_name = Column(String)

    product_name = Column(String)

    program_name = Column(String)

    test_name = Column(String)

    result = Column(String)

    measured_value = Column(Float)

    lower_limit = Column(Float)

    upper_limit = Column(Float)

    unit = Column(String)

    test_point = Column(String)

    timestamp = Column(DateTime)
class ICTBoardEvent(Base):

    __tablename__ = "ict_board_events"

    id = Column(Integer, primary_key=True)

    timestamp = Column(DateTime)

    board_name = Column(String)

    board_family = Column(String)

    program_name = Column(String)

    # ----------------------
    # Quality
    # ----------------------

    total_tests = Column(Integer)

    passed_tests = Column(Integer)

    failed_tests = Column(Integer)

    pass_rate = Column(Float)

    fail_rate = Column(Float)

    # ----------------------
    # Measurements
    # ----------------------

    avg_measurement = Column(Float)

    median_measurement = Column(Float)

    std_measurement = Column(Float)

    variance_measurement = Column(Float)

    min_measurement = Column(Float)

    max_measurement = Column(Float)

    measurement_range = Column(Float)

    # ----------------------
    # Margins
    # ----------------------

    avg_margin = Column(Float)

    median_margin = Column(Float)

    std_margin = Column(Float)

    min_margin = Column(Float)

    max_margin = Column(Float)

    # ----------------------
    # Metadata
    # ----------------------

    measured_tests = Column(Integer)

    missing_measurements = Column(Integer)

    duplicate_tests = Column(Integer)

    unique_tests = Column(Integer)
class FeatureVectorEntity(Base):

    __tablename__ = "feature_vectors"

    id = Column(Integer, primary_key=True)

    machine_type = Column(String)

    board_name = Column(String)

    board_family = Column(String)

    program_name = Column(String)

    timestamp = Column(DateTime)

    features = Column(JSON)