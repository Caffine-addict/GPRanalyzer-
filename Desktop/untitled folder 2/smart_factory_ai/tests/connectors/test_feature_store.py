from pathlib import Path

from machine_types.ict.parser import ICTParser

from machine_types.ict.normalizer import ICTNormalizer

from feature_store.service import FeatureStoreService


files = sorted(

    Path("data/raw/ICT").glob("*")

)

parser = ICTParser()

normalizer = ICTNormalizer()

board = parser.parse(

    str(files[0])

)

board = normalizer.normalize(

    board

)

service = FeatureStoreService()

vector = service.build(

    "ICT",

    board

)

print()

print("=======================")
print("FEATURE VECTOR")
print("=======================")

print()

print(vector)