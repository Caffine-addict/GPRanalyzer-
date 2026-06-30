from itertools import count


class BoardGenerator:
    """
    Generates unique board IDs.
    """

    def __init__(self):
        self.counter = count(1)

    def next_board(self):
        board_number = next(self.counter)

        return f"BOARD-{board_number:06d}"