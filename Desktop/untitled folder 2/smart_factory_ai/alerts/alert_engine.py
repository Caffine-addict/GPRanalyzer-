from datetime import datetime

from database.db import SessionLocal
from database.schema import (
    Alert,
    MachineFeature
)


class AlertEngine:

    @staticmethod
    def evaluate():

        session = SessionLocal()

        features = (
            session.query(MachineFeature)
            .all()
        )

        for feature in features:

            if feature.health_score < 60:

                alert = Alert(
                    timestamp=datetime.utcnow(),

                    machine_id=feature.machine_id,

                    severity="WARNING",

                    alert_type="LOW_HEALTH",

                    message=f"Health score dropped to {feature.health_score}",

                    status="OPEN"
                )

                session.add(alert)

        session.commit()
        session.close()