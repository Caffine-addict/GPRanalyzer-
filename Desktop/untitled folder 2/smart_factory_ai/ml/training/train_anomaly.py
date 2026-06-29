import pandas as pd
import joblib
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT_DIR))
from sklearn.ensemble import IsolationForest

from database.db import SessionLocal
from database.schema import Telemetry


def load_data():

    session = SessionLocal()

    records = session.query(Telemetry).all()

    rows = []

    for r in records:

        rows.append({
            "temperature": r.temperature,
            "vibration": r.vibration,
            "current": r.current,
            "rpm": r.rpm
        })

    session.close()

    return pd.DataFrame(rows)


def train():

    df = load_data()

    print(f"\nRows Loaded: {len(df)}")

    X = df[
        [
            "temperature",
            "vibration",
            "current",
            "rpm"
        ]
    ]

    model = IsolationForest(
        contamination=0.05,
        random_state=42
    )

    model.fit(X)

    joblib.dump(
        model,
        "ml/models/anomaly_model.pkl"
    )

    print("\nModel Saved")


if __name__ == "__main__":
    train()