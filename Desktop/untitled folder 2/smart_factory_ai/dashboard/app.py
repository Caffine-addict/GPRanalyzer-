import sys
from pathlib import Path
from services.alert_service import AlertService
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR))

import pandas as pd
import plotly.express as px
import streamlit as st
from services.fleet_service import FleetService
from streamlit_autorefresh import st_autorefresh

from services.feature_service import FeatureService
from services.anomaly_service import AnomalyService
from services.telemetry_service import TelemetryService

st.set_page_config(
    page_title="Smart Factory AI",
    layout="wide"
)

# Auto refresh every 5 seconds
st_autorefresh(
    interval=5000,
    key="factory_refresh"
)

st.title(" Smart Factory AI Dashboard")

feature = FeatureService.get_latest()
events = AnomalyService.get_latest()
telemetry = TelemetryService.get_latest(200)

# -----------------------------
# TOP KPIs
# -----------------------------

col1, col2, col3, col4 = st.columns(4)

if feature:

    health = feature.health_score

    if health >= 90:
        status = "NORMAL"
    elif health >= 75:
        status = "WARNING"
    elif health >= 50:
        status = "CRITICAL"
    else:
        status = "FAILURE"

    col1.metric(
        "Machine",
        feature.machine_id
    )

    col2.metric(
        "Health Score",
        round(feature.health_score, 2)
    )

    col3.metric(
        "Temperature",
        round(feature.avg_temperature, 2)
    )

    col4.metric(
        "Status",
        status
    )

st.divider()

# -----------------------------
# HEALTH BAR
# -----------------------------

st.subheader("Machine Health")

if feature:

    st.progress(
        min(int(feature.health_score), 100)
    )

    st.write(
        f"Health Score: {feature.health_score:.2f}"
    )

# -----------------------------
# TELEMETRY CHARTS
# -----------------------------

st.subheader("Telemetry Trends")

if telemetry:

    rows = []

    for t in reversed(telemetry):

        rows.append(
            {
                "timestamp": t.timestamp,
                "temperature": t.temperature,
                "vibration": t.vibration,
                "current": t.current,
                "rpm": t.rpm
            }
        )

    df = pd.DataFrame(rows)

    c1, c2 = st.columns(2)

    with c1:

        fig = px.line(
            df,
            x="timestamp",
            y="temperature",
            title="Temperature Trend"
        )

        st.plotly_chart(
            fig,
            use_container_width=True
        )

    with c2:

        fig = px.line(
            df,
            x="timestamp",
            y="vibration",
            title="Vibration Trend"
        )

        st.plotly_chart(
            fig,
            use_container_width=True
        )

    c3, c4 = st.columns(2)

    with c3:

        fig = px.line(
            df,
            x="timestamp",
            y="current",
            title="Current Trend"
        )

        st.plotly_chart(
            fig,
            use_container_width=True
        )

    with c4:

        fig = px.line(
            df,
            x="timestamp",
            y="rpm",
            title="RPM Trend"
        )

        st.plotly_chart(
            fig,
            use_container_width=True
        )

# -----------------------------
# ANOMALY TABLE
# -----------------------------

st.subheader("Recent Anomaly Events")

rows = []

for e in events:

    rows.append(
        {
            "Machine": e.machine_id,
            "Score": round(e.anomaly_score, 4),
            "Prediction": e.prediction,
            "Timestamp": e.timestamp
        }
    )

if rows:

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True
    )
alerts = AlertService.get_open_alerts()
st.subheader("Open Alerts")

rows = []

for a in alerts:

    rows.append(
        {
            "Machine": a.machine_id,
            "Severity": a.severity,
            "Type": a.alert_type,
            "Message": a.message,
            "Status": a.status
        }
    )

if rows:
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True
    )