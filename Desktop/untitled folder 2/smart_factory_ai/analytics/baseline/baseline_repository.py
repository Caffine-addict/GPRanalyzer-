from database.db import SessionLocal
from database.schema import ICTBoardEvent


class BaselineRepository:

    def __init__(self):

        self.session = SessionLocal()

    def all_events(self):

        return (
            self.session
            .query(ICTBoardEvent)
            .order_by(ICTBoardEvent.timestamp.asc())
            .all()
        )

    def by_board_family(self, board_family):

        return (
            self.session
            .query(ICTBoardEvent)
            .filter(
                ICTBoardEvent.board_family == board_family
            )
            .order_by(ICTBoardEvent.timestamp.asc())
            .all()
        )

    def by_program(self, program):

        return (
            self.session
            .query(ICTBoardEvent)
            .filter(
                ICTBoardEvent.program_name == program
            )
            .order_by(ICTBoardEvent.timestamp.asc())
            .all()
        )