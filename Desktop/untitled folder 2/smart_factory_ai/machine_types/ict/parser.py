from datetime import datetime

from machine_types.ict.models import (
    ICTBoard,
    ICTTest,
)


class ICTParser:

    @staticmethod
    def safe_float(value):

        if value is None or value == "":
            return None

        try:
            return float(value)
        except ValueError:
            return None

    @staticmethod
    def safe_int(value):

        if value is None or value == "":
            return None

        try:
            return int(value)
        except ValueError:
            return None

    def parse(self, filepath):

        board = None

        with open(
            filepath,
            "r",
            encoding="utf-8",
            errors="ignore"
        ) as file:

            for line in file:

                line = line.strip()

                if not line:
                    continue

                parts = line.split(";")

                record = parts[0]

                # ---------------------------------
                # START RECORD
                # ---------------------------------

                if record == "START":

                    # Expected format:
                    #
                    # START;
                    # BoardName;
                    # ProductName;
                    # Revision;
                    # ProductNumber;
                    # ProgramName;
                    # 06/24/2026;
                    # 12:55:00

                    timestamp = datetime.strptime(
                        f"{parts[6]} {parts[7]}",
                        "%m/%d/%Y %H:%M:%S"
                    )

                    board = ICTBoard(

                        board_name=parts[1],

                        product_name=parts[2],

                        revision=parts[3],

                        product_number=parts[4],

                        # For ICT we use board_name as family.
                        # Later we'll derive this automatically.
                        board_family=parts[1],

                        program_name=parts[5],

                        timestamp=timestamp,

                        tests=[]
                    )

                # ---------------------------------
                # ANALOG TEST RECORD
                # ---------------------------------

                elif record == "ANL":

                    if board is None:
                        continue

                    test = ICTTest(

                        sequence=self.safe_int(parts[1]),

                        test_name=parts[2],

                        test_id=self.safe_int(parts[3]),

                        retry=self.safe_int(parts[4]),

                        description=parts[5],

                        status=parts[7],

                        measured_value=self.safe_float(parts[8]),

                        low_limit=self.safe_float(parts[9]),

                        high_limit=self.safe_float(parts[10]),

                        unit=parts[11] if len(parts) > 11 else None,

                        test_points=parts[12] if len(parts) > 12 else None,

                        execution_order=self.safe_int(parts[13])
                        if len(parts) > 13 else None

                    )

                    board.tests.append(test)

        if board is None:
            raise ValueError(
                f"No START record found in {filepath}"
            )

        return board