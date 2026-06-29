import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR))

from database.db import SessionLocal
from database.schema import Telemetry


class TelemetryService:

    @staticmethod
    def get_latest(limit=100):

        session = SessionLocal()

        records = (
            session.query(Telemetry)
            .order_by(Telemetry.id.desc())
            .limit(limit)
            .all()
        )

        session.close()

        return records
@staticmethod
def get_latest(limit=100):

    session = SessionLocal()

    records = (
        session.query(Telemetry)
        .order_by(Telemetry.id.desc())
        .limit(limit)
        .all()
    )

    session.close()

    return records