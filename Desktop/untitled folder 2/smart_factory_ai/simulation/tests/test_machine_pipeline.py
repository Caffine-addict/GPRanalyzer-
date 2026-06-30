from simulation.models.machine import Machine

from simulation.models.station import Station

from simulation.pipeline.machine_pipeline import MachinePipeline


def test_machine_pipeline():

    machine = Machine("ICT-01")

    station = Station("ICT", machine)

    pipeline = MachinePipeline()

    event = pipeline.process(

        machine,

        station,

        "BOARD-000001"

    )

    assert event.board_id == "BOARD-000001"

    assert machine.processed_boards == 1

    assert machine.throughput == 1