"""The harness: an agent that builds test environments for other agents.

It reads an agent, works out what it verifiably is, builds a world its tools can run against,
generates scenarios, runs them, and reads the results back. Each of those is a stage, each stage
is its own session, and stages hand work to each other as artifacts on disk.

The split that matters: the model does judgement, and code decides outcomes. Reading unfamiliar
source, designing a schema, and choosing what is worth testing are judgement. Executing a tool
call and grading a run are not, and are never delegated to a model.

Stages are described in files under ``skills/``, so the method is editable without touching
code, and where an agent comes from is a registered source, so a new kind of agent is a class
rather than a new code path.
"""

from .chat import Conversation, open_conversation
from .config import (
    DEFAULT_MODEL,
    artifact_dir,
    load_skill,
    provider_env,
    read_only_session,
)
from .contract import AgentContract, ToolSpec, validate_contract
from .scenario import Scenario, validate_scenario
from .session import Stage, Turn
from .sources import (
    AgentSource,
    GitHubSource,
    RepoSource,
    SpecSource,
    register_source,
    resolve,
    supported,
)
from .understand import open_stage, understand

__all__ = [
    "AgentContract",
    "AgentSource",
    "Conversation",
    "DEFAULT_MODEL",
    "GitHubSource",
    "RepoSource",
    "Scenario",
    "SpecSource",
    "Stage",
    "ToolSpec",
    "Turn",
    "artifact_dir",
    "load_skill",
    "open_conversation",
    "open_stage",
    "provider_env",
    "read_only_session",
    "register_source",
    "resolve",
    "supported",
    "understand",
    "validate_contract",
    "validate_scenario",
]
