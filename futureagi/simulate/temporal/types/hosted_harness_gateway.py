from dataclasses import dataclass


@dataclass(frozen=True)
class HostedHarnessGatewayInput:
    job_id: str
    endpoint_base_url: str
    max_infrastructure_attempts: int


@dataclass(frozen=True)
class HostedHarnessGatewayOutput:
    job_id: str
    state: str


@dataclass(frozen=True)
class HostedHarnessAttemptInput:
    attempt_id: str


@dataclass(frozen=True)
class HostedHarnessLaunchOutput:
    attempt_id: str


@dataclass(frozen=True)
class HostedHarnessPollOutput:
    done: bool
    state: str
