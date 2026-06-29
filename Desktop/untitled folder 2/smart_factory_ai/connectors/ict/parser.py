from connectors.ict.schema import (
    ICTTestRecord,
    ICTBoardResult
)


class ICTParser:

    def __init__(self, filepath):

        self.filepath = filepath

    def safe_float(self, value):

        try:
            return float(value)
        except:
            return None

    def parse(self):

        board_name = None
        program_name = None
        timestamp = None

        records = []

        passed = 0
        failed = 0

        with open(
            self.filepath,
            "r",
            encoding="utf-8",
            errors="ignore"
        ) as f:

            for line in f:

                line = line.strip()

                if not line:
                    continue

                parts = line.split(";")

                # -------------------------
                # HEADER RECORD
                # -------------------------
                if parts[0] == "START":

                    board_name = (
                        parts[1]
                        if len(parts) > 1
                        else None
                    )

                    program_name = (
                        parts[2]
                        if len(parts) > 2
                        else None
                    )

                    if len(parts) > 7:

                        timestamp = (
                            parts[6]
                            + " "
                            + parts[7]
                        )

                # -------------------------
                # ANALOG TEST RECORD
                # -------------------------
                elif parts[0] == "ANL":

                    try:

                        test_name = (
                            parts[2]
                            if len(parts) > 2
                            else None
                        )

                        result = (
                            parts[7]
                            if len(parts) > 7
                            else None
                        )

                        if result == "PASS":
                            passed += 1
                        else:
                            failed += 1

                        measured = None
                        low_limit = None
                        high_limit = None
                        unit = None

                        if len(parts) > 11:

                            measured = self.safe_float(
                                parts[8]
                            )

                            low_limit = self.safe_float(
                                parts[9]
                            )

                            high_limit = self.safe_float(
                                parts[10]
                            )

                            unit = parts[11]

                        record = ICTTestRecord(
                            test_type="ANL",
                            test_name=test_name,
                            result=result,
                            measured_value=measured,
                            low_limit=low_limit,
                            high_limit=high_limit,
                            unit=unit,
                            test_id=parts[-1]
                        )

                        records.append(record)

                    except Exception:
                        continue

        return ICTBoardResult(
            board_name=board_name,
            program_name=program_name,
            timestamp=timestamp,
            total_tests=len(records),
            passed_tests=passed,
            failed_tests=failed,
            records=records
        )