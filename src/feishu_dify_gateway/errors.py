from __future__ import annotations


class GatewayError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.status_code = status_code
