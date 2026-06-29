from machine_types.ict.event_repository import (
    ICTEventRepository
)

from analytics.quality.quality_engine import (
    QualityEngine
)

repo = ICTEventRepository()

event = repo.latest()

engine = QualityEngine()

result = engine.evaluate(event)

print()

print("======================")
print("QUALITY")
print("======================")

print(result)