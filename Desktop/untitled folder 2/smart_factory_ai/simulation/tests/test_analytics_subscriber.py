from simulation.subscribers.analytics_subscriber import AnalyticsSubscriber

from simulation.events.board_event import BoardEvent

from datetime import datetime


def test_analytics_subscriber():

    subscriber = AnalyticsSubscriber()

    event = BoardEvent(

        board_id="BOARD-000001",

        machine="ICT-01",

        station="ICT",

        status="PASS",

        timestamp=datetime.now()

    )

    subscriber(event)