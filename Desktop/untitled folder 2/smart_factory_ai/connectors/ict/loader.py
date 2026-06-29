from pathlib import Path

import pandas as pd

from connectors.ict.parser import ICTParser


class ICTLoader:

    def __init__(self, folder_path):

        self.folder_path = Path(folder_path)

    def load_all(self):

        rows = []

        files = list(
            self.folder_path.glob("*")
        )

        for file in files:

            if not file.is_file():
                continue

            try:

                parser = ICTParser(
                    str(file)
                )

                result = parser.parse()
                print("\nFILE:", file.name)
                print("BOARD:", result.board_name)
                print("PROGRAM:", result.program_name)
                print("TIMESTAMP:", result.timestamp)

                for record in result.records:

                    rows.append(
                        {
                            "file_name": file.name,

                            "board_name":
                                result.board_name,

                            "program_name":
                                result.program_name,

                            "timestamp":
                                result.timestamp,

                            "test_name":
                                record.test_name,

                            "result":
                                record.result,

                            "measured_value":
                                record.measured_value,

                            "low_limit":
                                record.low_limit,

                            "high_limit":
                                record.high_limit,

                            "unit":
                                record.unit,

                            "test_id":
                                record.test_id
                        }
                    )

            except Exception as e:

                print(
                    f"Failed: {file.name}"
                )

                print(e)

        return pd.DataFrame(rows)