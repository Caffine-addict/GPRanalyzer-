from sqlalchemy import func
from datetime import datetime

from analytics.health.health_score import HealthScoreCalculator
from database.db import SessionLocal
from database.schema import Telemetry, MachineFeature


class FeatureBuilder:

    def __init__(self):
        self.session = SessionLocal()

    def build_features(self, machine_id):

        records = (
            self.session
            .query(Telemetry)
            .filter(
                Telemetry.machine_id == machine_id
            )
            .all()
        )

        if len(records) == 0:
            return None

        avg_temp = (
            self.session.query(
                func.avg(Telemetry.temperature)
            )
            .filter(
                Telemetry.machine_id == machine_id
            )
            .scalar()
        )

        avg_vibration = (
            self.session.query(
                func.avg(Telemetry.vibration)
            )
            .filter(
                Telemetry.machine_id == machine_id
            )
            .scalar()
        )

        avg_current = (
            self.session.query(
                func.avg(Telemetry.current)
            )
            .filter(
                Telemetry.machine_id == machine_id
            )
            .scalar()
        )

        avg_rpm = (
            self.session.query(
                func.avg(Telemetry.rpm)
            )
            .filter(
                Telemetry.machine_id == machine_id
            )
            .scalar()
        )

        health_score = (
            HealthScoreCalculator.calculate(
                avg_temp,
                avg_vibration,
                avg_current,
                avg_rpm
            )
        )

        return {
            "machine_id": machine_id,
            "avg_temperature": round(avg_temp, 2),
            "avg_vibration": round(avg_vibration, 2),
            "avg_current": round(avg_current, 2),
            "avg_rpm": round(avg_rpm, 2),
            "health_score": health_score
        }

    def save_features(self, feature_data):

        feature = MachineFeature(
            timestamp=datetime.utcnow(),

            machine_id=feature_data["machine_id"],

            avg_temperature=feature_data["avg_temperature"],
            avg_vibration=feature_data["avg_vibration"],
            avg_current=feature_data["avg_current"],
            avg_rpm=feature_data["avg_rpm"],

            health_score=feature_data["health_score"],

            anomaly_risk=0
        )

        self.session.add(feature)
        self.session.commit()