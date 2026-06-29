from pathlib import Path

from machine_types.ict.parser import ICTParser
from machine_types.ict.normalizer import ICTNormalizer
from machine_types.ict.feature_engine import ICTFeatureEngine
from machine_types.ict.event_builder import ICTEventBuilder


files = sorted(Path("data/raw/ICT").iterdir())

board = ICTParser().parse(str(files[0]))

normalized = ICTNormalizer().normalize(board)

features = ICTFeatureEngine().build(normalized)

event = ICTEventBuilder().build(
    normalized,
    features
)

print()

print("========================")
print("BOARD EVENT")
print("========================")

print(event)