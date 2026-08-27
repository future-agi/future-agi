"""Pure planning and state management for FutureAGI development slots."""

from .catalog import SUPPORTED_SERVICES, expand_services, parse_services
from .models import REGISTRY_VERSION, Registry, SlotRecord, StateIdentity
from .provisioning import ProvisionPlan, PurgePlan, StateCommand
from .registry import RegistryStore, discover_state_dir
from .runtime import SlotRuntime
from .state import (
    INFRA_ENGINES,
    StatePlan,
    StateValidationError,
    adapt_orchestrator_state,
    build_state_plan,
)

__all__ = [
    "INFRA_ENGINES",
    "REGISTRY_VERSION",
    "SUPPORTED_SERVICES",
    "ProvisionPlan",
    "PurgePlan",
    "Registry",
    "RegistryStore",
    "SlotRecord",
    "SlotRuntime",
    "StateCommand",
    "StateIdentity",
    "StatePlan",
    "StateValidationError",
    "adapt_orchestrator_state",
    "build_state_plan",
    "discover_state_dir",
    "expand_services",
    "parse_services",
]
