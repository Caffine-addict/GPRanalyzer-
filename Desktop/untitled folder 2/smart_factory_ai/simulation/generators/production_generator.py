from simulation.generators.board_generator import BoardGenerator


class ProductionGenerator:

    def __init__(self):

        self.generator = BoardGenerator()

    def next_board(self):

        return self.generator.next_board()