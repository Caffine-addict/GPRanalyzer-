from simulation.generators.board_generator import BoardGenerator


def test_board_generator():

    generator = BoardGenerator()

    assert generator.next_board() == "BOARD-000001"

    assert generator.next_board() == "BOARD-000002"

    assert generator.next_board() == "BOARD-000003"