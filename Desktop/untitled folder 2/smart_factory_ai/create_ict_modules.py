# create_ict_modules.py

from pathlib import Path

folders = [
    "machine_types/ict",
    "machine_types/aoi",
    "machine_types/smt",
    "machine_types/tht",
    "analytics/quality",
    "analytics/correlation",
    "analytics/drift",
    "ingestion/file_ingestion",
    "docs/datasets",
]

files = [
    "machine_types/ict/parser.py",
    "machine_types/ict/schema.py",
    "machine_types/ict/feature_builder.py",
    "machine_types/ict/quality_engine.py",
    "machine_types/ict/trend_engine.py",

    "analytics/quality/quality_metrics.py",
    "analytics/drift/drift_engine.py",
    "analytics/correlation/root_cause_engine.py",

    "docs/datasets/ict.md",
]

for folder in folders:
    Path(folder).mkdir(parents=True, exist_ok=True)

for file in files:
    Path(file).touch(exist_ok=True)

print("ICT architecture created successfully.")