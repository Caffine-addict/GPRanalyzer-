import pandas as pd


class ICTFeatureBuilder:

    def build(self, df):

        boards = []

        for file_name, group in df.groupby("file_name"):

            total_tests = len(group)

            passed_tests = (
                group["result"] == "PASS"
            ).sum()

            failed_tests = (
                group["result"] != "PASS"
            ).sum()

            pass_rate = (
                passed_tests / total_tests
            ) * 100

            numeric = group[
                group["measured_value"].notna()
            ].copy()

            avg_value = (
                numeric["measured_value"]
                .mean()
            )

            margins = []

            for _, row in numeric.iterrows():

                low = row["low_limit"]
                high = row["high_limit"]
                value = row["measured_value"]

                if (
                    low is not None
                    and high is not None
                    and high > low
                ):
                    margin = min(
                        value - low,
                        high - value
                    )

                    margins.append(margin)

            avg_margin = (
                sum(margins) / len(margins)
                if margins
                else 0
            )

            min_margin = (
                min(margins)
                if margins
                else 0
            )

            boards.append(
                {
                    "file_name": file_name,

                    "board_name":
                        group["board_name"]
                        .iloc[0],

                    "program_name":
                        group["program_name"]
                        .iloc[0],

                    "timestamp":
                        group["timestamp"]
                        .iloc[0],

                    "total_tests":
                        total_tests,

                    "passed_tests":
                        passed_tests,

                    "failed_tests":
                        failed_tests,

                    "pass_rate":
                        pass_rate,

                    "avg_measured_value":
                        avg_value,

                    "avg_margin":
                        avg_margin,

                    "min_margin":
                        min_margin
                }
            )

        return pd.DataFrame(boards)