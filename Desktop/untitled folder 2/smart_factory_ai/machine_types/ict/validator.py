from dataclasses import dataclass


@dataclass
class ValidationResult:

    valid: bool

    errors: list[str]


class ICTValidator:

    def validate(

        self,

        board

    ):

        errors = []

        if board is None:

            errors.append(

                "Board is None."

            )

            return ValidationResult(

                False,

                errors

            )

        if board.board_name == "":

            errors.append(

                "Missing board name."

            )

        if board.program_name == "":

            errors.append(

                "Missing program."

            )

        if len(board.tests) == 0:

            errors.append(

                "No ICT tests found."

            )

        return ValidationResult(

            len(errors) == 0,

            errors

        )