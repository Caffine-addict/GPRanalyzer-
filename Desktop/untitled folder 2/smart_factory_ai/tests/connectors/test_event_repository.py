from pathlib import Path

from machine_types.ict.parser import ICTParser
from machine_types.ict.normalizer import ICTNormalizer
from machine_types.ict.feature_engine import ICTFeatureEngine
from machine_types.ict.event_builder import ICTEventBuilder
from machine_types.ict.event_repository import ICTEventRepository


files = sorted(Path("data/raw/ICT").iterdir())

board = ICTParser().parse(str(files[0]))

normalized = ICTNormalizer().normalize(board)

features = ICTFeatureEngine().build(normalized)

event = ICTEventBuilder().build(
    normalized,
    features
)

repo = ICTEventRepository()

saved = repo.save(event)

print()

print("========================")
print("DATABASE EVENT")
print("========================")

print("ID          :", saved.id)
print("Board       :", saved.board_name)
print("Program     :", saved.program_name)
print("Pass Rate   :", saved.pass_rate)
print("Avg Margin  :", saved.avg_margin)

print()

latest = repo.latest()

print("Latest ID :", latest.id)