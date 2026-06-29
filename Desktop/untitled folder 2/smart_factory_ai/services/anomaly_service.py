import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR))

from database.db import SessionLocal
from database.schema import AnomalyEvent


class AnomalyService:

    @staticmethod
    def get_latest(limit=50):

        session = SessionLocal()

        records = (
            session.query(AnomalyEvent)
            .order_by(AnomalyEvent.id.desc())
            .limit(limit)
            .all()
        )

        session.close()

        return records