from machine_types.ict.feature_engine import ICTFeatureEngine


class FeatureRegistry:

    def __init__(self):

        self._engines = {

            "ICT": ICTFeatureEngine()

        }

    def get_engine(self, machine_type):

        return self._engines[machine_type]