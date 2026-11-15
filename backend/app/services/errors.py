"""Errors the services raise and the routers turn into HTTP responses (404, 409, 422)."""


class NotFound(Exception):
    pass


class Conflict(Exception):
    """Not allowed in the current state, or the record changed since the caller read it."""


class Invalid(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems
