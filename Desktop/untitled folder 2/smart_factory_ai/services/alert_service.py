from database.db import SessionLocal
from database.schema import Alert


class AlertService:

    @staticmethod
    def get_open_alerts():

        session = SessionLocal()

        alerts = (
            session.query(Alert)
            .filter(
                Alert.status == "OPEN"
            )
            .all()
        )

        session.close()

        return alerts