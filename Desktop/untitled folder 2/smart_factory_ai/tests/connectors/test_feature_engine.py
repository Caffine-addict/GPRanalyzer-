from pathlib import Path

from machine_types.ict.parser import ICTParser
from machine_types.ict.normalizer import ICTNormalizer
from machine_types.ict.feature_engine import ICTFeatureEngine


files = sorted(Path("data/raw/ICT").iterdir())

board = ICTParser().parse(str(files[0]))

normalized = ICTNormalizer().normalize(board)

features = ICTFeatureEngine().build(normalized)

print()

print("=========================")
print("ICT FEATURES")
print("=========================")

print(features)