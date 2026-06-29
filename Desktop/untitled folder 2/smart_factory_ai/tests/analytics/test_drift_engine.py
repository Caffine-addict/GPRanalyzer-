from machine_types.ict.event_repository import (
    ICTEventRepository
)

from analytics.drift.drift_engine import (
    DriftEngine
)


repo = ICTEventRepository()

event = repo.latest()

engine = DriftEngine()

result = engine.evaluate(event)

print()

print("======================")
print("DRIFT")
print("======================")

print(result)
