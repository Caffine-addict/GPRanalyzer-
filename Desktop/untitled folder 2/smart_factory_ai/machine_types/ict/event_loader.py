from pathlib import Path

from machine_types.ict.parser import ICTParser
from machine_types.ict.validator import ICTValidator
from machine_types.ict.normalizer import ICTNormalizer
from machine_types.ict.feature_engine import ICTFeatureEngine
from machine_types.ict.event_builder import ICTEventBuilder
from machine_types.ict.event_repository import ICTEventRepository


class ICTEventLoader:

    def __init__(self):

        self.parser = ICTParser()

        self.validator = ICTValidator()

        self.normalizer = ICTNormalizer()

        self.feature_engine = ICTFeatureEngine()

        self.builder = ICTEventBuilder()

        self.repository = ICTEventRepository()

    def load_file(self, filepath):

        board = self.parser.parse(filepath)

        validation = self.validator.validate(board)

        if not validation.valid:

            raise ValueError(validation.errors)

        normalized = self.normalizer.normalize(board)

        features = self.feature_engine.build(normalized)

        event = self.builder.build(
            normalized,
            features
        )

        self.repository.save(event)

        return event

    def load_folder(self, folder):

        events = []

        files = sorted(Path(folder).iterdir())

        for file in files:

            if not file.is_file():
                continue

            try:

                event = self.load_file(str(file))

                events.append(event)

            except Exception as e:

                print(f"Skipped {file.name}: {e}")

        return events