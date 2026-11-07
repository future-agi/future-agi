"""Stable domain errors translated into API envelopes by the view layer."""


class SkillsHubError(Exception):
    def __init__(
        self, code: str, message: str, *, status: int, detail: dict | None = None
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail or {}
