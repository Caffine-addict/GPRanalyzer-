import streamlit as st
from api import SmartFactoryAPI

st.set_page_config(
    page_title="Smart Factory AI",
    page_icon="🏭",
    layout="wide"
)

api = SmartFactoryAPI()

data = api.latest_analysis()

st.title("🏭 Smart Factory AI Dashboard")

st.markdown("---")

c1, c2, c3, c4 = st.columns(4)

with c1:
    st.metric(
        "Health Score",
        data["health"]["score"],
        border=True
    )

with c2:
    st.metric(
        "Risk Level",
        data["risk"]["risk_level"],
        border=True
    )

with c3:
    st.metric(
        "Pass Rate",
        f'{data["quality"]["pass_rate"]:.2f}%',
        border=True
    )

with c4:
    st.metric(
        "Boards",
        data["baseline"]["boards"],
        border=True
    )

st.markdown("---")

left, right = st.columns([2,1])

with left:

    st.subheader("Recommendations")

    for rec in data["recommendation"]["recommendations"]:
        st.success(rec)

with right:

    st.subheader("Alerts")

    for alert in data["alerts"]:
        st.error(
            f'{alert["severity"]}\n\n{alert["message"]}'
        )

st.markdown("---")

st.subheader("System Status")

health = data["health"]["health"]

risk = data["risk"]["risk_level"]

quality = data["quality"]["status"]

col1, col2, col3 = st.columns(3)

with col1:
    st.info(f"Machine Health : {health}")

with col2:
    st.warning(f"Quality : {quality}")

with col3:
    st.error(f"Risk : {risk}")