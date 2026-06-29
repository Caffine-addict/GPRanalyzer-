from machine_types.ict.event_repository import ICTEventRepository

from analytics.risk.risk_engine import RiskEngine


repo = ICTEventRepository()

event = repo.latest()

engine = RiskEngine()

result = engine.evaluate(event)

print()

print("===================")
print("RISK")
print("===================")

print(result)