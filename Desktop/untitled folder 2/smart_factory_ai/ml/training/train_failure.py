import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT_DIR))

import pandas as pd
import joblib

from sklearn.ensemble import RandomForestClassifier

from database.db import SessionLocal
from database.schema import Telemetry


def create_label(status):

    mapping = {
        "NORMAL": 0,
        "WARNING": 1,
        "CRITICAL": 2,
        "FAILURE": 3
    }

    return mapping.get(status, 0)


def load_data():

    session = SessionLocal()

    rows = []

    telemetry = session.query(Telemetry).all()

    for t in telemetry:

        rows.append(
            {
                "temperature": t.temperature,
                "vibration": t.vibration,
                "current": t.current,
                "rpm": t.rpm,
                "label": create_label(t.status)
            }
        )

    session.close()

    return pd.DataFrame(rows)


def train():

    df = load_data()

    print(
        f"Rows Loaded: {len(df)}"
    )

    X = df[
        [
            "temperature",
            "vibration",
            "current",
            "rpm"
        ]
    ]

    y = df["label"]

    model = RandomForestClassifier(
        n_estimators=100,
        random_state=42
    )

    model.fit(X, y)

    joblib.dump(
        model,
        "ml/models/failure_model.pkl"
    )

    print("Failure Model Saved")


if __name__ == "__main__":
    train()