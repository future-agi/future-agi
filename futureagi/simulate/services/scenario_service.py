"""Service layer for scenario operations shared by API and internal AI workflows."""

from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ServiceError:
    message: str
    code: str = "ERROR"


def create_scenario(
    *,
    name,
    description,
    scenario_type,
    source_type,
    agent_definition_id=None,
    agent_version_id=None,
    dataset_id=None,
    organization,
    workspace,
    user,
    source="",
    no_of_rows=20,
    custom_instruction="",
    personas=None,
    add_persona_automatically=False,
    custom_columns=None,
    generate_graph=True,
    graph=None,
    script_url=None,
    prompt_template_id=None,
    prompt_version_id=None,
):
    """Create a new scenario for agent testing.

    Supports all scenario types (dataset, graph, script) and both
    agent_definition and prompt source types. For each type,
    a Temporal workflow is started to generate the scenario data asynchronously.

    Returns:
        dict with scenario info or ServiceError
    """
    from model_hub.models.choices import StatusType
    from simulate.models import AgentDefinition, Scenarios, SimulatorAgent
    from simulate.utils.test_execution_utils import generate_simulator_agent_prompt

    agent_def = None
    agent_version = None
    prompt_template = None
    prompt_version = None

    if source_type == "prompt":
        # Validate prompt template and version
        from model_hub.models.run_prompt import (
            PromptTemplate,
            PromptVersion,
        )

        try:
            pt_filters = {
                "id": prompt_template_id,
                "deleted": False,
                "organization": organization,
            }
            if workspace:
                pt_filters["workspace"] = workspace
            prompt_template = PromptTemplate.objects.get(**pt_filters)
        except PromptTemplate.DoesNotExist:
            return ServiceError(
                f"Prompt template {prompt_template_id} not found.", "NOT_FOUND"
            )

        try:
            pv_filters = {
                "id": prompt_version_id,
                "deleted": False,
            }
            prompt_version = PromptVersion.objects.get(**pv_filters)
        except PromptVersion.DoesNotExist:
            return ServiceError(
                f"Prompt version {prompt_version_id} not found.", "NOT_FOUND"
            )

        # Create simulator agent with generic prompt for prompt-based scenarios
        try:
            agent_prompt = generate_simulator_agent_prompt(agent_definition=None)
        except Exception:
            agent_prompt = "You are a simulator agent for prompt testing."

        simulator_agent = SimulatorAgent.objects.create(
            name=name,
            prompt=agent_prompt,
            organization=organization,
            workspace=workspace,
        )
    else:
        # Validate agent definition
        if not agent_definition_id:
            return ServiceError(
                "agent_definition_id is required for agent_definition source type.",
                "VALIDATION_ERROR",
            )

        try:
            agent_def = AgentDefinition.objects.get(
                id=agent_definition_id,
                organization=organization,
                deleted=False,
            )
        except AgentDefinition.DoesNotExist:
            return ServiceError(
                f"Agent definition {agent_definition_id} not found.", "NOT_FOUND"
            )

        # Validate agent version if provided
        if agent_version_id:
            from simulate.models import AgentVersion

            try:
                agent_version = AgentVersion.objects.get(
                    id=agent_version_id,
                    agent_definition=agent_def,
                    organization=organization,
                    deleted=False,
                )
            except AgentVersion.DoesNotExist:
                return ServiceError(
                    f"Agent version {agent_version_id} not found.", "NOT_FOUND"
                )

        # Create simulator agent with full config
        try:
            agent_prompt = generate_simulator_agent_prompt(
                agent_version=agent_version, agent_definition=agent_def
            )
        except Exception:
            agent_prompt = f"You are testing the agent: {agent_def.agent_name}"

        simulator_agent = SimulatorAgent.objects.create(
            name=name,
            prompt=agent_prompt,
            organization=organization,
            workspace=workspace,
        )

    # Validate dataset if provided
    if dataset_id:
        from model_hub.models.develop_dataset import Dataset

        try:
            Dataset.objects.get(id=dataset_id, deleted=False)
        except Dataset.DoesNotExist:
            return ServiceError(f"Dataset {dataset_id} not found.", "NOT_FOUND")

    # Map scenario type
    type_mapping = {
        "dataset": Scenarios.ScenarioTypes.DATASET,
        "script": Scenarios.ScenarioTypes.SCRIPT,
        "graph": Scenarios.ScenarioTypes.GRAPH,
    }
    mapped_type = type_mapping.get(scenario_type, Scenarios.ScenarioTypes.DATASET)

    # Build metadata
    metadata = {}
    if agent_version_id:
        metadata["agent_definition_version_id"] = str(agent_version_id)
    if personas:
        metadata["persona_ids"] = [str(pid) for pid in personas]
    if custom_instruction:
        metadata["custom_instruction"] = custom_instruction

    # All scenario types start as PROCESSING — workflows generate data async
    scenario = Scenarios.objects.create(
        name=name,
        description=description,
        source=source or "Processing...",
        scenario_type=mapped_type,
        source_type=source_type,
        organization=organization,
        workspace=workspace,
        dataset=None,
        simulator_agent=simulator_agent,
        agent_definition=agent_def,
        prompt_template=prompt_template,
        prompt_version=prompt_version,
        status=StatusType.PROCESSING.value,
        metadata=metadata,
    )

    # Build validated_data for the workflow (mirrors what the API view passes)
    validated_data = {
        "name": name,
        "description": description,
        "kind": scenario_type,
        "source_type": source_type,
        "no_of_rows": no_of_rows,
    }
    if agent_definition_id:
        validated_data["agent_definition_id"] = str(agent_definition_id)
    if agent_version_id:
        validated_data["agent_definition_version_id"] = str(agent_version_id)
    if dataset_id:
        validated_data["dataset_id"] = str(dataset_id)
    if custom_instruction:
        validated_data["custom_instruction"] = custom_instruction
    if personas:
        validated_data["personas"] = [str(pid) for pid in personas]
    if add_persona_automatically:
        validated_data["add_persona_automatically"] = True
    if custom_columns:
        validated_data["custom_columns"] = custom_columns
    if generate_graph:
        validated_data["generate_graph"] = True
    if graph:
        validated_data["graph"] = graph
    if script_url:
        validated_data["script_url"] = script_url
    if prompt_template_id:
        validated_data["prompt_template_id"] = str(prompt_template_id)
    if prompt_version_id:
        validated_data["prompt_version_id"] = str(prompt_version_id)

    # Start the appropriate workflow based on scenario type
    workflow_started = False
    try:
        if scenario_type == "dataset":
            from tfc.temporal.simulate import (
                start_create_dataset_scenario_workflow_sync,
            )

            start_create_dataset_scenario_workflow_sync(
                user_id=user.id,
                validated_data=validated_data,
                scenario_id=str(scenario.id),
            )
            workflow_started = True

        elif scenario_type == "graph":
            from tfc.temporal.simulate import (
                start_create_graph_scenario_workflow_sync,
            )

            start_create_graph_scenario_workflow_sync(
                validated_data=validated_data,
                scenario_id=str(scenario.id),
            )
            workflow_started = True

        elif scenario_type == "script":
            from tfc.temporal.simulate import (
                start_create_script_scenario_workflow_sync,
            )

            start_create_script_scenario_workflow_sync(
                validated_data=validated_data,
                scenario_id=str(scenario.id),
            )
            workflow_started = True

    except Exception as e:
        logger.warning("failed_to_start_scenario_workflow", error=str(e))

    return {
        "scenario": scenario,
        "id": str(scenario.id),
        "name": scenario.name,
        "type": scenario_type,
        "agent_id": str(agent_definition_id) if agent_definition_id else None,
        "status": scenario.status,
        "is_dataset_scenario": scenario_type in ("dataset", "graph", "script"),
        "workflow_started": workflow_started,
    }
