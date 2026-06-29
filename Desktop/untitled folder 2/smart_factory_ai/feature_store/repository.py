from database.db import SessionLocal
from database.schema import FeatureVectorEntity


class FeatureRepository:

    def __init__(self):

        self.session = SessionLocal()

    def save(self, vector):

        row = FeatureVectorEntity(

            machine_type=vector.machine_type,

            board_name=vector.board_name,

            board_family=vector.board_family,

            program_name=vector.program_name,

            timestamp=vector.timestamp,

            features=vector.features

        )

        self.session.add(row)

        self.session.commit()

        return row

    def latest(self):

        return (

            self.session.query(

                FeatureVectorEntity

            )

            .order_by(

                FeatureVectorEntity.timestamp.desc()

            )

            .first()

        )

    def all(self):

        return self.session.query(

            FeatureVectorEntity

        ).all()