import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR))

from database.db import SessionLocal
from database.schema import (
    MachineFeature,
    AnomalyEvent
)

from analytics.trends.risk_engine import RiskEngine


class FleetService:

    @staticmethod
    def get_machine_ranking():

        session = SessionLocal()

        features = (
            session.query(MachineFeature)
            .all()
        )

        events = (
            session.query(AnomalyEvent)
            .all()
        )

        latest_anomaly = {}

        for e in events:
            latest_anomaly[e.machine_id] = e

        ranking = []

        for f in features:

            score = 0

            if f.machine_id in latest_anomaly:

                score = latest_anomaly[
                    f.machine_id
                ].anomaly_score

            risk = RiskEngine.calculate_risk(
                f.health_score,
                score
            )

            ranking.append(
                {
                    "machine": f.machine_id,
                    "health": f.health_score,
                    "anomaly_score": score,
                    "risk": risk
                }
            )

        session.close()

        ranking.sort(
            key=lambda x: x["risk"],
            reverse=True
        )

        return ranking