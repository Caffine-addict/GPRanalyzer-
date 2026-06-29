from pathlib import Path

from machine_types.ict.parser import ICTParser
from machine_types.ict.normalizer import ICTNormalizer

files = sorted(Path("data/raw/ICT").iterdir())

board = ICTParser().parse(str(files[0]))

normalized = ICTNormalizer().normalize(board)

print()

print("BOARD :", normalized.board_name)

print("TOTAL :", normalized.total_tests)

print("PASS  :", normalized.passed_tests)

print("FAIL  :", normalized.failed_tests)

print()

print(normalized.tests[0])