from smart_factory_ai.feature_store.TelemeteryFeatureBuilder import FeatureBuilder

builder = FeatureBuilder()

machines = [
    "M001",
    "M002",
    "M003"
]

for machine in machines:

    features = builder.build_features(machine)

    if features:

        builder.save_features(features)

        print(
            f"Saved features for {machine}"
        )