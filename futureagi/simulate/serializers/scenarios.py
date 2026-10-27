# Backward-compatibility shim.
# Production code imports from:
#   simulate.serializers.requests.scenarios  (request/input serializers)
#   simulate.serializers.response.scenarios  (response/output serializers)
#
# This file re-exports under the old names so existing tests and any external
# callers continue to work without modification.

from simulate.serializers.requests.scenarios import (
    ScenarioAddColumnsRequestSerializer as AddScenarioColumnsSerializer,
    ScenarioAddRowsRequestSerializer as AddScenarioRowsSerializer,
    ScenarioCreateRequestSerializer as CreateScenarioSerializer,
    ScenarioEditPromptsRequestSerializer as EditScenarioPromptsSerializer,
    ScenarioEditRequestSerializer as EditScenarioSerializer,
)
from simulate.serializers.response.scenarios import ScenarioResponseSerializer

# Backward-compat alias — tests import ScenariosSerializer for output serialization.
# Production views now use ScenarioResponseSerializer directly.
ScenariosSerializer = ScenarioResponseSerializer

__all__ = [
    "ScenariosSerializer",
    "CreateScenarioSerializer",
    "EditScenarioSerializer",
    "EditScenarioPromptsSerializer",
    "AddScenarioRowsSerializer",
    "AddScenarioColumnsSerializer",
]
