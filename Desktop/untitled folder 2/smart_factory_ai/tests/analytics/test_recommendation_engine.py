from machine_types.ict.event_repository import ICTEventRepository

from analytics.context.analysis_service import AnalysisService


repo = ICTEventRepository()

event = repo.latest()

service = AnalysisService()

context = service.build(event)

print()

print("======================")
print("RECOMMENDATION")
print("======================")

print()

print(context.recommendation)