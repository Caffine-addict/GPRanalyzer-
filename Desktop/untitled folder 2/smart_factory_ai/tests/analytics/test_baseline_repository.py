from analytics.baseline.baseline_repository import BaselineRepository


repo = BaselineRepository()

events = repo.all_events()

print()

print("=======================")
print("DATABASE")
print("=======================")

print("Total Events :", len(events))

print()

print(events[0].board_name)

print(events[-1].board_name)
