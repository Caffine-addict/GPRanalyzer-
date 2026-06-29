from connectors.ict.loader import ICTLoader
from connectors.ict.feature_builder import (
    ICTFeatureBuilder
)

loader = ICTLoader(
    "data/raw/ict"
)

df = loader.load_all()

builder = ICTFeatureBuilder()

features = builder.build(df)

print(features.head())

print()

print(features.shape)