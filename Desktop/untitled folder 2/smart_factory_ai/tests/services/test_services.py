from services.feature_service import FeatureService
from services.anomaly_service import AnomalyService

feature = FeatureService.get_latest()

print(feature.machine_id)
print(feature.health_score)

events = AnomalyService.get_latest()

for e in events:
    print(
        e.machine_id,
        e.anomaly_score,
        e.prediction
    )