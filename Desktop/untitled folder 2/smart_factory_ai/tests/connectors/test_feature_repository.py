from feature_store.repository import FeatureRepository

repo = FeatureRepository()

latest = repo.latest()

print()
print("======================")
print("LATEST FEATURE VECTOR")
print("======================")
print()

print(latest.machine_type)
print(latest.board_family)
print(latest.program_name)
print()

print(latest.features)