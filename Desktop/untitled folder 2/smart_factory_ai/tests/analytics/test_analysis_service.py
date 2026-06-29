from machine_types.ict.event_repository import (
    ICTEventRepository
)

from analytics.context.analysis_service import (
    AnalysisService
)


repo = ICTEventRepository()

event = repo.latest()

service = AnalysisService()

context = service.build(event)

print()
print("========================")
print("ANALYSIS CONTEXT")
print("========================")

print()

print("Baseline")
print(context.baseline)

print()

print("Quality")
print(context.quality)

print()

print("Drift")
print(context.drift)

print()

print("Health")
print(context.health)

print()

print("Risk")
print(context.risk)