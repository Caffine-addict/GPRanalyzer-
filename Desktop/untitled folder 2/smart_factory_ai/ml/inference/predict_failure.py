import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT_DIR))

import joblib
import pandas as pd

model = joblib.load(
    "ml/models/failure_model.pkl"
)

sample = pd.DataFrame([
    {
        "temperature": 95,
        "vibration": 3,
        "current": 10,
        "rpm": 900
    }
])

prediction = model.predict(sample)[0]

probabilities = model.predict_proba(sample)[0]

failure_probability = (
    probabilities[3] * 100
)
print(
    "Failure State:",
    prediction
)

print(
    "Failure Probability:",
    round(
        failure_probability,
        2
    ),
    "%"
)

print(
    "Failure State:",
    prediction[0]
)