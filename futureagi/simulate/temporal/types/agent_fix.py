"""What AgentFixWorkflow and its activities pass each other. No Django imports: the
workflow imports this module, and so does simulate.services.agent_fix_sandbox."""

from dataclasses import dataclass
from enum import StrEnum


class Observation(StrEnum):
    """What the platform last saw of a fix and the engine working on it."""

    # The fix is held back by the running-fix caps; it has no sandbox yet.
    WAITING = "waiting"
    # The engine runs in its sandbox.
    RUNNING = "running"
    # The run reached its ending; nothing is left to do.
    ENDED = "ended"
    # The user asked the run to stop.
    STOPPING = "stopping"
    # The engine process exited or stopped reporting while the run still runs.
    ENGINE_GONE = "engine_gone"


@dataclass(frozen=True)
class AgentFixInput:
    fix_run_id: str


@dataclass(frozen=True)
class AgentFixLaunch:
    # RUNNING when an engine was started; otherwise why the run gets no sandbox.
    observation: Observation
    # The engine process in its sandbox, when one was started.
    command_id: str = ""


@dataclass(frozen=True)
class AgentFixWatchInput:
    fix_run_id: str
    command_id: str


@dataclass(frozen=True)
class AgentFixWatch:
    observation: Observation


@dataclass(frozen=True)
class AgentFixCloseInput:
    fix_run_id: str
    # An AgentFixRun.State ending.
    state: str
    code: str = ""
    message: str = ""


@dataclass(frozen=True)
class AgentFixOutput:
    fix_run_id: str
    observation: Observation
