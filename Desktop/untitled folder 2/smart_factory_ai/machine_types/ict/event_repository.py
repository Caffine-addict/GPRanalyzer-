from database.db import SessionLocal
from database.schema import ICTBoardEvent


class ICTEventRepository:

    def __init__(self):

        self.session = SessionLocal()

    def save(self, event):

        db_event = ICTBoardEvent(

            timestamp=event.timestamp,

            board_name=event.board_name,

            board_family=event.board_family,

            program_name=event.program_name,

            total_tests=event.total_tests,

            passed_tests=event.passed_tests,

            failed_tests=event.failed_tests,

            pass_rate=event.pass_rate,

            fail_rate=event.fail_rate,

            avg_measurement=event.avg_measurement,

            median_measurement=event.median_measurement,

            std_measurement=event.std_measurement,

            variance_measurement=event.variance_measurement,

            min_measurement=event.min_measurement,

            max_measurement=event.max_measurement,

            measurement_range=event.measurement_range,

            avg_margin=event.avg_margin,

            median_margin=event.median_margin,

            std_margin=event.std_margin,

            min_margin=event.min_margin,

            max_margin=event.max_margin,

            measured_tests=event.measured_tests,

            missing_measurements=event.missing_measurements,

            duplicate_tests=event.duplicate_tests,

            unique_tests=event.unique_tests

        )

        self.session.add(db_event)

        self.session.commit()

        self.session.refresh(db_event)

        return db_event

    def latest(self):

        return (

            self.session.query(ICTBoardEvent)

            .order_by(ICTBoardEvent.id.desc())

            .first()

        )

    def all(self):

        return (

            self.session.query(ICTBoardEvent)

            .order_by(ICTBoardEvent.id.asc())

            .all()

        )