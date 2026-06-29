from analytics.baseline.baseline_engine import (
    BaselineEngine
)

engine = BaselineEngine()

baseline = engine.build(
    "SAG_GOA_OS_2"
)

print()

print("===================")
print("BASELINE")
print("===================")

for k, v in baseline.items():

    print(k, ":", v)