from simulation.bus.event_bus import EventBus


def test_event_bus():

    received = []

    bus = EventBus()

    def callback(payload):

        received.append(payload)

    bus.subscribe(

        "test",

        callback

    )

    bus.publish(

        "test",

        123

    )

    assert received == [123]
    