from connectors.ict.parser import ICTParser


parser = ICTParser(
    "data/raw/ict/GF2605059984"
)

result = parser.parse()

print()

print("Board:", result.board_name)

print("Program:", result.program_name)

print("Timestamp:", result.timestamp)

print("Total Tests:", result.total_tests)

print("Pass:", result.passed_tests)

print("Fail:", result.failed_tests)

print()

print(result.records[0])
