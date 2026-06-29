from pathlib import Path

from machine_types.ict.parser import ICTParser
from machine_types.ict.validator import ICTValidator


files = sorted(Path("data/raw/ICT").iterdir())

assert len(files) > 0, "No ICT files found."

parser = ICTParser()

board = parser.parse(str(files[0]))

validator = ICTValidator()

result = validator.validate(board)

print("\n==============================")
print("BOARD")
print("==============================")

print("Board Name :", board.board_name)
print("Program    :", board.program_name)
print("Timestamp  :", board.timestamp)

print()

print("Total Tests :", board.total_tests)
print("Passed      :", board.passed_tests)
print("Failed      :", board.failed_tests)

print()

print("Validation :", result.valid)

if result.errors:
    print(result.errors)

print()

print("First Test")

print(board.tests[0])
