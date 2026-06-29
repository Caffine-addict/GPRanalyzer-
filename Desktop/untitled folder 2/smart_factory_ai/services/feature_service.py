import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR))

from database.db import SessionLocal
from database.schema import MachineFeature


class FeatureService:

    @staticmethod
    def get_latest():

        session = SessionLocal()

        feature = (
            session.query(MachineFeature)
            .order_by(MachineFeature.id.desc())
            .first()
        )

        session.close()

        return feature