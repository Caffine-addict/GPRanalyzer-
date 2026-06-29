from feature_store.models import FeatureVector

from feature_store.registry import FeatureRegistry

from feature_store.repository import FeatureRepository
class FeatureStoreService:

    def __init__(self):

        self.registry = FeatureRegistry()
        self.repository = FeatureRepository()

    def build(self, machine_type, board):

        engine = self.registry.get_engine(

            machine_type

        )

        features = engine.build(

            board

        )

        vector = FeatureVector(

            machine_type=machine_type,

            board_name=board.board_name,

            board_family=board.board_family,

            program_name=board.program_name,

            timestamp=board.timestamp,

            features=features.__dict__

        )

        self.repository.save(vector)

        return vector