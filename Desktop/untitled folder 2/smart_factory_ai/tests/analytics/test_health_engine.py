from machine_types.ict.event_repository import ICTEventRepository

from analytics.health.health_engine import (
    HealthEngine
)

repo = ICTEventRepository()

event = repo.latest()

engine = HealthEngine()

result = engine.evaluate(event)

print()

print("======================")
print("HEALTH")
print("======================")

print(result)
