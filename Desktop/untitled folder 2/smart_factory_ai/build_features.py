from smart_factory_ai.feature_store.TelemeteryFeatureBuilder import FeatureBuilder

builder = FeatureBuilder()

features = builder.build_features("M001")

builder.save_features(features)

print("\nFeatures Saved\n")

print(features)