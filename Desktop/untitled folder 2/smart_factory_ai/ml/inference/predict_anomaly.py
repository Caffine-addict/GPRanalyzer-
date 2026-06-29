import sys
from pathlib import Path
from datetime import datetime

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT_DIR))

import joblib
import pandas as pd

from database.db import SessionLocal
from database.schema import (
    MachineFeature,
    AnomalyEvent
)

MODEL_PATH = "ml/models/anomaly_model.pkl"


def get_latest_feature():

    session = SessionLocal()

    feature = (
        session.query(MachineFeature)
        .order_by(MachineFeature.id.desc())
        .first()
    )

    session.close()

    return feature


def save_anomaly_event(machine_id, score, prediction):

    session = SessionLocal()

    event = AnomalyEvent(
        timestamp=datetime.utcnow(),
        machine_id=machine_id,
        anomaly_score=float(score),
        prediction=int(prediction)
    )

    session.add(event)
    session.commit()

    session.close()


def predict():

    model = joblib.load(MODEL_PATH)

    feature = get_latest_feature()

    if feature is None:
        print("No feature records found.")
        return

    df = pd.DataFrame([
        {
            "temperature": feature.avg_temperature,
            "vibration": feature.avg_vibration,
            "current": feature.avg_current,
            "rpm": feature.avg_rpm
        }
    ])

    prediction = model.predict(df)[0]

    score = model.decision_function(df)[0]

    save_anomaly_event(
        feature.machine_id,
        score,
        prediction
    )

    print("\n==============================")
    print(f"Machine: {feature.machine_id}")
    print(f"Prediction: {prediction}")
    print(f"Anomaly Score: {score:.4f}")

    if prediction == -1:
        print("ANOMALY DETECTED")
    else:
        print("NORMAL")

    print("==============================\n")


if __name__ == "__main__":
    predict()