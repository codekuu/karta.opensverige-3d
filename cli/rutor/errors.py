"""Exit-coded errors for the rutor command.

Codes: 1 bad input, 2 refused by the service, 3 environment, 4 network.
"""

from __future__ import annotations


class RutorError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def bad_input(message: str) -> RutorError:
    return RutorError(1, message)


def refused(message: str) -> RutorError:
    return RutorError(2, message)


def environment(message: str) -> RutorError:
    return RutorError(3, message)


def network(message: str) -> RutorError:
    return RutorError(4, message)
