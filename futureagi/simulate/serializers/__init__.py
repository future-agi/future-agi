# Serializers package for simulate app

# Request serializers
from .requests.agent_definition import (
    AgentDefinitionBulkDeleteRequestSerializer,
    AgentDefinitionCreateRequestSerializer,
    AgentDefinitionEditRequestSerializer,
    AgentDefinitionFilterSerializer,
    FetchAssistantRequestSerializer,
)
from .requests.agent_version import (
    AgentVersionCreateRequestSerializer,
)

# Other serializers
from .requests.run_test import (
    CreateRunTestSerializer,
    UpdateRunTestSerializer,
)

# Response serializers
from .response.agent_definition import (
    AgentDefinitionBulkDeleteResponseSerializer,
    AgentDefinitionCreateResponseSerializer,
    AgentDefinitionDeleteResponseSerializer,
    AgentDefinitionDetailResponseSerializer,
    AgentDefinitionEditResponseSerializer,
    AgentDefinitionListResponseSerializer,
    AgentDefinitionResponseSerializer,
    FetchAssistantResponseSerializer,
)
from .response.agent_version import (
    AgentVersionActivateResponseSerializer,
    AgentVersionCreateResponseSerializer,
    AgentVersionDeleteResponseSerializer,
    AgentVersionListResponseSerializer,
    AgentVersionResponseSerializer,
    AgentVersionRestoreResponseSerializer,
)
from .run_test import (
    RunTestSerializer,
)
from .scenarios import (
    AddScenarioRowsSerializer,
    CreateScenarioSerializer,
    EditScenarioSerializer,
    ScenariosSerializer,
)
from .simulator_agent import SimulatorAgentSerializer
from .test_execution import (
    AllActiveTestsSerializer,
    CallExecutionSerializer,
    CallTranscriptSerializer,
    TestExecutionCancelSerializer,
    TestExecutionRequestSerializer,
    TestExecutionSerializer,
    TestExecutionStatusSerializer,
)

# from .requests.persona import (
#     PersonaCreateRequestSerializer,
#     PersonaDuplicateRequestSerializer,
#     PersonaFilterSerializer,
#     PersonaUpdateRequestSerializer,
# )


# from .response.persona import (
#     PersonaDeleteResponseSerializer,
#     PersonaFieldOptionsSerializer,
#     PersonaListSerializer,
#     PersonaResponseSerializer,
# )


__all__ = [
    "ScenariosSerializer",
    "CreateScenarioSerializer",
    "EditScenarioSerializer",
    "AddScenarioRowsSerializer",
    # Request serializers
    "AgentDefinitionCreateRequestSerializer",
    "AgentDefinitionEditRequestSerializer",
    "AgentDefinitionBulkDeleteRequestSerializer",
    "AgentDefinitionFilterSerializer",
    "FetchAssistantRequestSerializer",
    "AgentVersionCreateRequestSerializer",
    # Response serializers
    "AgentDefinitionResponseSerializer",
    "AgentDefinitionCreateResponseSerializer",
    "AgentDefinitionEditResponseSerializer",
    "AgentDefinitionListResponseSerializer",
    "AgentDefinitionDetailResponseSerializer",
    "AgentDefinitionBulkDeleteResponseSerializer",
    "AgentDefinitionDeleteResponseSerializer",
    "FetchAssistantResponseSerializer",
    "AgentVersionResponseSerializer",
    "AgentVersionListResponseSerializer",
    "AgentVersionCreateResponseSerializer",
    "AgentVersionActivateResponseSerializer",
    "AgentVersionDeleteResponseSerializer",
    "AgentVersionRestoreResponseSerializer",
    # Persona
    # "PersonaResponseSerializer",
    # "PersonaListSerializer",
    # "PersonaCreateRequestSerializer",
    # "PersonaUpdateRequestSerializer",
    # "PersonaDuplicateRequestSerializer",
    # "PersonaFilterSerializer",
    # "PersonaDeleteResponseSerializer",
    # "PersonaFieldOptionsSerializer",
    # Other
    "SimulatorAgentSerializer",
    "RunTestSerializer",
    "CreateRunTestSerializer",
    "UpdateRunTestSerializer",
    "TestExecutionSerializer",
    "CallExecutionSerializer",
    "CallTranscriptSerializer",
    "TestExecutionStatusSerializer",
    "TestExecutionRequestSerializer",
    "TestExecutionCancelSerializer",
    "AllActiveTestsSerializer",
]
