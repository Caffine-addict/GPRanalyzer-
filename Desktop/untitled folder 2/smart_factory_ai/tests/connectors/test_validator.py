from connectors.ict.parser import ICTParser
from machine_types.ict.validator import ICTValidator


parser = ICTParser(
    "data/raw/ict/GF2605059984"
)

board = parser.parse()

validator = ICTValidator()

report = validator.validate(board)

print("=" * 60)

print("VALID:", report.is_valid)

print()

print("STATISTICS")

for k, v in report.statistics.items():

    print(f"{k:25} : {v}")

print()

print("ERRORS")

for error in report.errors:

    print(error)

print()

print("WARNINGS")

for warning in report.warnings:

    print(warning)