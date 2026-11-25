"""What AgentVersionWorkflow and its activities pass each other. No Django imports."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentVersionInput:
    version_id: str


@dataclass(frozen=True)
class AgentVersionStep:
    # done, skipped or failed.
    status: str
    code: str = ""
    message: str = ""
    retryable: bool = False


@dataclass(frozen=True)
class AgentVersionFailure:
    version_id: str
    code: str
    message: str = ""
    retryable: bool = False


@dataclass(frozen=True)
class AgentVersionOutput:
    version_id: str
    status: str
