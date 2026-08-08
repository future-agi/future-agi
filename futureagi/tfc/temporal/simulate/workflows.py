"""
Temporal workflows for simulate app.

These workflows orchestrate scenario generation and management.

Note: Test execution workflows have been removed - using Celery tasks instead.

Workflow Hierarchy:
    ScenarioGenerationWorkflow (standalone)
    └── Activities: setup, generate, validate, persist

    AddScenarioColumnsWorkflow (standalone)
    └── Activities: setup, generate, persist

    CreateDatasetScenarioWorkflow (standalone)
    CreateScriptScenarioWorkflow (standalone)
    CreateGraphScenarioWorkflow (standalone)
"""

from datetime import timedelta
from typing import Any, List

from temporalio import workflow
from temporalio.common import RetryPolicy

# Import types from separate module (no Django imports, safe for sandbox)
with workflow.unsafe.imports_passed_through():
    from tfc.temporal.simulate.types import (  # Scenario Generation; Scenario Creation; Graph Scenario Sub-Activity Types (v2+v3)
        AddColumnsWorkflowInput,
        AddColumnsWorkflowOutput,
        AddScenarioColumnsInput,
        AddScenarioRowsWorkflowInput,
        AddScenarioRowsWorkflowOutput,
        CategorizeAndValidateInput,
        CreateDatasetScenarioWorkflowInput,
        CreateDatasetScenarioWorkflowOutput,
        CreateGraphScenarioWorkflowInput,
        CreateGraphScenarioWorkflowOutput,
        CreateScenarioDatasetInput,
        CreateScriptScenarioWorkflowInput,
        CreateScriptScenarioWorkflowOutput,
        FinalizeGraphScenarioInput,
        GenerateCasesForIntentInput,
        GenerateColumnDataInput,
        GenerateScenarioRowsInput,
        GenerateSyntheticDataInput,
        PersistCellsInput,
        PersistColumnCellsInput,
        PrepareScenarioInput,
        ProcessBranchesInput,
        ScenarioGenerationWorkflowInput,
        ScenarioGenerationWorkflowOutput,
        SetupColumnsInput,
        SetupGenerationInput,
        SetupGraphScenarioInput,
        ValidateAndEnrichCasesInput,
        ValidatePersonasInput,
    )


# =============================================================================
# Retry Policies
# =============================================================================

# For setup activities (fast, few retries)
SETUP_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=3,
    backoff_coefficient=2.0,
)

# For LLM calls (handles rate limits)
LLM_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=10),
    maximum_interval=timedelta(minutes=5),
    maximum_attempts=3,
    backoff_coefficient=2.0,
    non_retryable_error_types=["ValueError"],
)

# For pure-transform activities (fast, minimal retries)
TRANSFORM_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=5),
    maximum_attempts=2,
    backoff_coefficient=2.0,
)

# =============================================================================
# Helper Functions
# =============================================================================


def get_result_field(result: Any, field: str, default: Any = None) -> Any:
    """Get a field from an activity result, handling both dict and dataclass."""
    if isinstance(result, dict):
        return result.get(field, default)
    return getattr(result, field, default)


# =============================================================================
# Scenario Generation Workflow
# =============================================================================


@workflow.defn
class ScenarioGenerationWorkflow:
    """
    Workflow to generate scenario rows with synthetic data.

    Orchestrates:
    1. Setup generation (load dataset, extract branches)
    2. Generate synthetic data (persona, situation, outcome)
    3. Validate personas
    4. Persist cells to database
    """

    @workflow.run
    async def run(
        self, input: ScenarioGenerationWorkflowInput
    ) -> ScenarioGenerationWorkflowOutput:
        # NOTE: Do NOT use workflow.logger - it causes deadlocks due to stdlib logging locks

        try:
            # Step 1: Setup generation
            setup_result = await workflow.execute_activity(
                "setup_generation_activity",
                SetupGenerationInput(
                    dataset_id=input.dataset_id,
                    scenario_id=input.scenario_id,
                    num_rows=input.num_rows,
                ),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=SETUP_RETRY_POLICY,
            )

            if get_result_field(setup_result, "status") != "READY":
                error = get_result_field(setup_result, "error", "Setup failed")
                return ScenarioGenerationWorkflowOutput(
                    dataset_id=input.dataset_id,
                    scenario_id=input.scenario_id,
                    status="FAILED",
                    error=error,
                )

            generation_payload = get_result_field(
                setup_result, "generation_payload", {}
            )
            branch_metadata = get_result_field(setup_result, "branch_metadata", [])
            column_names = get_result_field(setup_result, "column_names", [])

            # Step 2: Generate synthetic data
            generate_result = await workflow.execute_activity(
                "generate_synthetic_data_activity",
                GenerateSyntheticDataInput(
                    generation_payload=generation_payload,
                    branch_metadata=branch_metadata,
                    organization_id=input.organization_id,
                ),
                start_to_close_timeout=timedelta(hours=12),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=LLM_RETRY_POLICY,
            )

            if get_result_field(generate_result, "status") != "COMPLETED":
                error = get_result_field(generate_result, "error", "Generation failed")
                return ScenarioGenerationWorkflowOutput(
                    dataset_id=input.dataset_id,
                    scenario_id=input.scenario_id,
                    status="FAILED",
                    error=error,
                )

            data = get_result_field(generate_result, "data", [])

            # Step 3: Validate personas
            validate_result = await workflow.execute_activity(
                "validate_personas_activity",
                ValidatePersonasInput(
                    personas=[row.get("persona", {}) for row in data],
                    required_fields=[
                        "name",
                        "gender",
                        "age_group",
                        "location",
                        "profession",
                        "personality",
                        "communication_style",
                        "accent",
                        "language",
                        "conversation_speed",
                        "finished_speaking_sensitivity",
                        "interrupt_sensitivity",
                        "background_sound",
                    ],
                ),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=SETUP_RETRY_POLICY,
            )

            validated_personas = get_result_field(
                validate_result, "validated_personas", []
            )
            for i, persona in enumerate(validated_personas):
                if i < len(data):
                    data[i]["persona"] = persona

            # Step 4: Persist cells
            persist_result = await workflow.execute_activity(
                "persist_cells_activity",
                PersistCellsInput(
                    dataset_id=input.dataset_id,
                    row_ids=input.row_ids,
                    column_names=column_names,
                    data=data,
                ),
                start_to_close_timeout=timedelta(hours=12),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=SETUP_RETRY_POLICY,
            )

            if get_result_field(persist_result, "status") != "COMPLETED":
                error = get_result_field(persist_result, "error", "Persist failed")
                return ScenarioGenerationWorkflowOutput(
                    dataset_id=input.dataset_id,
                    scenario_id=input.scenario_id,
                    status="FAILED",
                    error=error,
                )

            return ScenarioGenerationWorkflowOutput(
                dataset_id=input.dataset_id,
                scenario_id=input.scenario_id,
                status="COMPLETED",
                rows_generated=input.num_rows,
            )

        except Exception as e:
            # Re-raise to mark workflow as Failed in Temporal UI
            raise


# =============================================================================
# Add Scenario Rows Workflow
# =============================================================================


@workflow.defn
class AddScenarioRowsWorkflow:
    """
    Workflow to add new rows to a scenario dataset.

    Uses the complete generate_scenario_rows activity which handles:
    - Setup generation (load dataset, extract branches)
    - Generate synthetic data (persona, situation, outcome)
    - Validate personas
    - Persist cells to database
    """

    @workflow.run
    async def run(
        self, input: AddScenarioRowsWorkflowInput
    ) -> AddScenarioRowsWorkflowOutput:
        # NOTE: Do NOT use workflow.logger - it causes deadlocks due to stdlib logging locks

        try:
            # Call the complete generate_scenario_rows activity
            # This function handles all steps: setup, generation, validation, and persistence
            await workflow.execute_activity(
                "generate_scenario_rows_activity",
                GenerateScenarioRowsInput(
                    dataset_id=input.dataset_id,
                    scenario_id=input.scenario_id,
                    num_rows=input.num_rows,
                    description=input.description,
                    row_ids=input.row_ids,
                    sample_size_reference_data=input.sample_size_reference_data,
                ),
                start_to_close_timeout=timedelta(hours=12),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=5),
                    maximum_interval=timedelta(seconds=30),
                    maximum_attempts=3,
                    backoff_coefficient=2.0,
                ),
            )

            return AddScenarioRowsWorkflowOutput(
                dataset_id=input.dataset_id,
                scenario_id=input.scenario_id,
                status="COMPLETED",
                rows_generated=input.num_rows,
            )

        except Exception as e:
            # Re-raise to mark workflow as Failed in Temporal UI
            raise


# =============================================================================
# Add Scenario Columns Workflow
# =============================================================================


@workflow.defn
class AddScenarioColumnsWorkflow:
    """
    Workflow to add new columns to a scenario dataset.

    Uses the complete add_scenario_columns_activity which handles:
    - Building generation payload from existing dataset context
    - Generating new column data using SyntheticDataAgent
    - Persisting cells to database
    - Updating column statuses
    """

    @workflow.run
    async def run(self, input: AddColumnsWorkflowInput) -> AddColumnsWorkflowOutput:
        # NOTE: Do NOT use workflow.logger - it causes deadlocks due to stdlib logging locks

        try:
            # Call the complete add_scenario_columns activity
            # This function handles all steps: setup, generation, and persistence
            await workflow.execute_activity(
                "add_scenario_columns_activity",
                AddScenarioColumnsInput(
                    dataset_id=input.dataset_id,
                    scenario_id=input.scenario_id,
                    columns_info=input.columns_info,
                    column_ids=input.column_ids,
                ),
                start_to_close_timeout=timedelta(hours=12),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=5),
                    maximum_interval=timedelta(seconds=30),
                    maximum_attempts=3,
                    backoff_coefficient=2.0,
                ),
            )

            return AddColumnsWorkflowOutput(
                dataset_id=input.dataset_id,
                status="COMPLETED",
                columns_added=len(input.columns_info),
            )

        except Exception as e:
            # Re-raise to mark workflow as Failed in Temporal UI
            raise


# =============================================================================
# Scenario Creation Workflows
# =============================================================================


@workflow.defn
class CreateDatasetScenarioWorkflow:
    """
    Workflow to create a dataset-based scenario.

    This is a wrapper workflow that calls create_dataset_scenario_activity.
    Replaces create_dataset_scenario_background_task.
    """

    @workflow.run
    async def run(
        self, input: CreateDatasetScenarioWorkflowInput
    ) -> CreateDatasetScenarioWorkflowOutput:
        # NOTE: Do NOT use workflow.logger - it causes deadlocks due to stdlib logging locks

        result = await workflow.execute_activity(
            "create_dataset_scenario_activity",
            input,
            start_to_close_timeout=timedelta(hours=12),
            heartbeat_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=5),
                maximum_interval=timedelta(seconds=30),
                maximum_attempts=3,
                backoff_coefficient=2.0,
            ),
        )

        return result


@workflow.defn
class CreateScriptScenarioWorkflow:
    """
    Workflow to create a script-based scenario.

    This is a wrapper workflow that calls create_script_scenario_activity.
    Replaces create_script_scenario_background_task.
    """

    @workflow.run
    async def run(
        self, input: CreateScriptScenarioWorkflowInput
    ) -> CreateScriptScenarioWorkflowOutput:
        # NOTE: Do NOT use workflow.logger - it causes deadlocks due to stdlib logging locks

        result = await workflow.execute_activity(
            "create_script_scenario_activity",
            input,
            start_to_close_timeout=timedelta(hours=12),
            heartbeat_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=5),
                maximum_interval=timedelta(seconds=30),
                maximum_attempts=3,
                backoff_coefficient=2.0,
            ),
        )

        return result


@workflow.defn
class CreateGraphScenarioWorkflow:
    """
    Workflow to create a graph-based scenario.

    v1 (legacy): Single activity that does everything
    v2: Multi-activity workflow with parallel intent processing
    v3 (current): Granular activities with Temporal-native parallelism

    Uses workflow.patched() for backward compatibility with in-flight workflows.
    """

    @workflow.run
    async def run(
        self, input: CreateGraphScenarioWorkflowInput
    ) -> CreateGraphScenarioWorkflowOutput:
        # NOTE: Do NOT use workflow.logger - it causes deadlocks due to stdlib logging locks

        # Use patched() for versioning - allows graceful migration
        if workflow.patched("v3-granular-activities"):
            return await self._run_v3_granular_activities(input)
        elif workflow.patched("v2-multi-activity-graph-scenario"):
            return await self._run_v2_multi_activity(input)
        else:
            # Legacy v1: single activity (for in-flight workflows)
            return await self._run_v1_single_activity(input)

    async def _run_v1_single_activity(
        self, input: CreateGraphScenarioWorkflowInput
    ) -> CreateGraphScenarioWorkflowOutput:
        """Legacy v1 implementation - single activity."""
        result = await workflow.execute_activity(
            "create_graph_scenario_activity",
            input,
            start_to_close_timeout=timedelta(hours=12),
            heartbeat_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=5),
                maximum_interval=timedelta(seconds=30),
                maximum_attempts=3,
                backoff_coefficient=2.0,
            ),
        )
        return result

    async def _run_v3_granular_activities(
        self, input: CreateGraphScenarioWorkflowInput
    ) -> CreateGraphScenarioWorkflowOutput:
        """
        v3 implementation — chunky pipeline with Temporal-native parallelism.

        5 steps (down from 9):
        1. prepare_scenario_activity — setup + intents + branches + metadata + select
        2. generate_cases_for_intent_activity (fan-out per intent, includes categorization)
        3. validate_and_enrich_cases_activity — pure transform
        4. create_scenario_dataset_activity — persist to DB
        5. finalize_graph_scenario_activity — update scenario status

        Branch-level parallelism uses ThreadPoolExecutor inside activities
        (not per-branch Temporal activities), reducing activity count from
        ~50-100 to ~M+4 where M = number of intents.
        """
        all_redis_keys: list = []  # Track Redis keys for cleanup on success or failure
        try:
            # Step 1: Prepare — setup, intents, branches, metadata, select
            prepare_result = await workflow.execute_activity(
                "prepare_scenario_activity",
                PrepareScenarioInput(
                    scenario_id=input.scenario_id,
                    validated_data=input.validated_data,
                ),
                start_to_close_timeout=timedelta(hours=1),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=SETUP_RETRY_POLICY,
            )

            if get_result_field(prepare_result, "status") != "COMPLETED":
                error = get_result_field(prepare_result, "error", "Preparation failed")
                raise RuntimeError(error)

            graph_id = get_result_field(prepare_result, "graph_id")
            agent_context = get_result_field(prepare_result, "agent_context", {})
            agent_definition_data = get_result_field(
                prepare_result, "agent_definition_data", {}
            )
            configuration_snapshot = get_result_field(
                prepare_result, "configuration_snapshot"
            )
            no_of_rows = get_result_field(prepare_result, "no_of_rows", 20)
            custom_columns = get_result_field(prepare_result, "custom_columns", [])
            property_list = get_result_field(prepare_result, "property_list", [])
            custom_instruction = get_result_field(prepare_result, "custom_instruction")
            mode = get_result_field(prepare_result, "mode", "voice")
            intent_dict = get_result_field(prepare_result, "intent_dict", {})

            # Claim-check: get Redis keys for large data (replaces inline metadata)
            selected_metadata_redis_key = get_result_field(
                prepare_result, "selected_metadata_redis_key"
            )
            branch_metadata_lookup_redis_key = get_result_field(
                prepare_result, "branch_metadata_lookup_redis_key"
            )
            # Inline fallback for in-flight v3 workflows (prepare ran with old code)
            selected_metadata_inline = (
                get_result_field(prepare_result, "selected_metadata") or []
            )
            branch_metadata_lookup_inline = (
                get_result_field(prepare_result, "branch_metadata_lookup") or {}
            )
            # Track all Redis keys for cleanup in finalize
            all_redis_keys = [
                k
                for k in [
                    selected_metadata_redis_key,
                    branch_metadata_lookup_redis_key,
                ]
                if k
            ]

            # Step 2: Generate cases — fan-out per intent
            # Each activity also categorizes its branches internally.
            # New activities store cases in Redis and return keys.
            # Fallback: also collect inline cases for compat with in-flight workflows.
            case_redis_keys: list = []
            all_inline_cases: list = []
            all_categorized_branches: dict = {}

            if intent_dict:
                num_intents = len(intent_dict)
                base_batch = no_of_rows // num_intents
                remainder = no_of_rows % num_intents
                intent_items = list(intent_dict.items())
                num_branches = get_result_field(prepare_result, "num_branches", 0)

                # Fire all intent activities in parallel via Temporal.
                # Worker's max_concurrent_activities controls actual concurrency.
                intent_handles = []
                for i, (intent_id, intent_value) in enumerate(intent_items):
                    batch_size = base_batch + (1 if i < remainder else 0)
                    if batch_size <= 0:
                        continue

                    # Compute branch subset for diversity.  When each intent
                    # has fewer rows than branches, the distribution logic
                    # (rows // num_branches) gives 0 to all but branch[0].
                    # Passing start_index + max_branches lets the activity
                    # slice after loading from Redis.
                    if num_branches > 0 and batch_size < num_branches:
                        branch_start = i % num_branches
                        max_branches = min(batch_size, num_branches)
                    else:
                        branch_start = 0
                        max_branches = 0  # 0 = use all

                    handle = workflow.start_activity(
                        "generate_cases_for_intent_activity",
                        GenerateCasesForIntentInput(
                            intent_id=intent_id,
                            intent_value=intent_value,
                            branches_metadata=selected_metadata_inline,
                            agent_definition_data=agent_definition_data,
                            batch_size=batch_size,
                            property_list=property_list,
                            custom_columns=custom_columns,
                            mode=mode,
                            graph_id=graph_id,
                            agent_context=agent_context,
                            custom_instruction=custom_instruction,
                            configuration_snapshot=configuration_snapshot,
                            selected_metadata_redis_key=selected_metadata_redis_key,
                            branch_start_index=branch_start,
                            max_branches=max_branches,
                        ),
                        start_to_close_timeout=timedelta(hours=6),
                        heartbeat_timeout=timedelta(minutes=5),
                        retry_policy=LLM_RETRY_POLICY,
                    )
                    intent_handles.append(handle)

                # Await all handles — activities are already running in parallel
                intent_results = []
                for handle in intent_handles:
                    try:
                        result = await handle
                        intent_results.append(result)
                    except Exception as e:
                        intent_results.append(e)

                for r in intent_results:
                    if isinstance(r, BaseException):
                        continue
                    if get_result_field(r, "status") == "COMPLETED":
                        # Prefer Redis key; fall back to inline cases (in-flight compat)
                        case_key = get_result_field(r, "cases_redis_key")
                        if case_key:
                            case_redis_keys.append(case_key)
                            all_redis_keys.append(case_key)
                        else:
                            inline = get_result_field(r, "cases") or []
                            all_inline_cases.extend(inline)
                        # Merge categorized branches (small dict, stays inline)
                        cb = get_result_field(r, "categorized_branches", {})
                        if cb:
                            all_categorized_branches.update(cb)
            else:
                # No intents — single generation with "default" intent
                gen_result = await workflow.execute_activity(
                    "generate_cases_for_intent_activity",
                    GenerateCasesForIntentInput(
                        intent_id="default",
                        intent_value="general",
                        branches_metadata=selected_metadata_inline,  # Inline fallback
                        agent_definition_data=agent_definition_data,
                        batch_size=no_of_rows,
                        property_list=property_list,
                        custom_columns=custom_columns,
                        mode=mode,
                        graph_id=graph_id,
                        agent_context=agent_context,
                        custom_instruction=custom_instruction,
                        configuration_snapshot=configuration_snapshot,
                        selected_metadata_redis_key=selected_metadata_redis_key,
                    ),
                    start_to_close_timeout=timedelta(hours=6),
                    heartbeat_timeout=timedelta(minutes=5),
                    retry_policy=LLM_RETRY_POLICY,
                )

                if get_result_field(gen_result, "status") == "COMPLETED":
                    case_key = get_result_field(gen_result, "cases_redis_key")
                    if case_key:
                        case_redis_keys.append(case_key)
                        all_redis_keys.append(case_key)
                    else:
                        inline = get_result_field(gen_result, "cases") or []
                        all_inline_cases.extend(inline)
                    cb = get_result_field(gen_result, "categorized_branches", {})
                    if cb:
                        all_categorized_branches.update(cb)

            if not case_redis_keys and not all_inline_cases:
                raise RuntimeError("No cases generated across all intents")

            # Step 3: Validate and enrich cases (pure transform)
            # Cases loaded from Redis (preferred) or passed inline (in-flight compat).
            validate_result = await workflow.execute_activity(
                "validate_and_enrich_cases_activity",
                ValidateAndEnrichCasesInput(
                    cases=all_inline_cases,  # Inline fallback for in-flight compat
                    categorized_branches=all_categorized_branches,
                    branch_metadata_lookup=branch_metadata_lookup_inline,  # Inline fallback
                    mode=mode,
                    custom_columns=custom_columns,
                    case_redis_keys=case_redis_keys if case_redis_keys else None,
                    branch_metadata_lookup_redis_key=branch_metadata_lookup_redis_key,
                ),
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=TRANSFORM_RETRY_POLICY,
            )

            if get_result_field(validate_result, "status") != "COMPLETED":
                error = get_result_field(validate_result, "error", "Validation failed")
                raise RuntimeError(error)

            validated_cases_redis_key = get_result_field(
                validate_result, "validated_cases_redis_key"
            )
            if validated_cases_redis_key:
                all_redis_keys.append(validated_cases_redis_key)
            # Inline fallback for in-flight compat
            validated_cases_inline = (
                get_result_field(validate_result, "validated_cases") or []
            )

            # Step 4: Create dataset and persist cases
            # Cases loaded from Redis (preferred) or passed inline (in-flight compat).
            scenario_name = input.validated_data.get("name", "Graph Scenario")
            scenario_description = input.validated_data.get("description", "")

            dataset_result = await workflow.execute_activity(
                "create_scenario_dataset_activity",
                CreateScenarioDatasetInput(
                    scenario_id=input.scenario_id,
                    cases=validated_cases_inline,  # Inline fallback
                    name=scenario_name,
                    description=scenario_description,
                    custom_columns=custom_columns,
                    agent_context=agent_context,
                    cases_redis_key=validated_cases_redis_key,
                ),
                start_to_close_timeout=timedelta(hours=1),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=SETUP_RETRY_POLICY,
            )

            if get_result_field(dataset_result, "status") != "COMPLETED":
                error = get_result_field(
                    dataset_result, "error", "Dataset creation failed"
                )
                raise RuntimeError(error)

            dataset_id = get_result_field(dataset_result, "dataset_id")

            # Step 5: Finalize scenario + clean up Redis keys.
            # add_persona_automatically=True drops user-picked personas so the
            # finalize write does not overwrite the empty property_list intent.
            persona_ids = (
                []
                if input.validated_data.get("add_persona_automatically", False)
                else input.validated_data.get("personas", [])
            )
            finalize_result = await workflow.execute_activity(
                "finalize_graph_scenario_activity",
                FinalizeGraphScenarioInput(
                    scenario_id=input.scenario_id,
                    dataset_id=dataset_id,
                    persona_ids=(
                        [str(pid) for pid in persona_ids] if persona_ids else None
                    ),
                    redis_keys_to_cleanup=all_redis_keys,
                ),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=SETUP_RETRY_POLICY,
            )

            return CreateGraphScenarioWorkflowOutput(
                scenario_id=get_result_field(
                    finalize_result, "scenario_id", input.scenario_id
                ),
                dataset_id=get_result_field(finalize_result, "dataset_id"),
                status=get_result_field(finalize_result, "status", "COMPLETED"),
                error=get_result_field(finalize_result, "error"),
            )

        except Exception as e:
            # On failure, try to finalize with FAILED status + clean up Redis
            error_msg = str(e)
            try:
                await workflow.execute_activity(
                    "finalize_graph_scenario_activity",
                    FinalizeGraphScenarioInput(
                        scenario_id=input.scenario_id,
                        dataset_id="",
                        redis_keys_to_cleanup=all_redis_keys,
                    ),
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=SETUP_RETRY_POLICY,
                )
            except Exception as finalize_err:
                error_msg = f"{error_msg}; finalize also failed: {finalize_err}"
            return CreateGraphScenarioWorkflowOutput(
                scenario_id=input.scenario_id,
                status="FAILED",
                error=error_msg,
            )

    async def _run_v2_multi_activity(
        self, input: CreateGraphScenarioWorkflowInput
    ) -> CreateGraphScenarioWorkflowOutput:
        """
        v2 implementation - multi-activity workflow with parallel intent processing.

        Steps:
        1. setup_graph_scenario_activity - Load scenario, build agent def, generate graph
        2. extract_intents_activity - Extract intents from transcripts or propose use cases
        3. process_branches_activity - Process branches and create metadata
        4. generate_cases_for_intent_activity (parallel) - Generate cases for each intent
        5. categorize_and_validate_activity - Categorize and validate all cases
        6. create_scenario_dataset_activity - Create dataset and persist cases
        7. finalize_graph_scenario_activity - Update scenario status
        """
        try:
            # Step 1: Setup - load scenario, build agent definition, generate/save graph
            setup_result = await workflow.execute_activity(
                "setup_graph_scenario_activity",
                SetupGraphScenarioInput(
                    scenario_id=input.scenario_id,
                    validated_data=input.validated_data,
                ),
                start_to_close_timeout=timedelta(minutes=30),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=SETUP_RETRY_POLICY,
            )

            if get_result_field(setup_result, "status") != "COMPLETED":
                error = get_result_field(setup_result, "error", "Setup failed")
                return CreateGraphScenarioWorkflowOutput(
                    scenario_id=input.scenario_id,
                    status="FAILED",
                    error=error,
                )

            graph_id = get_result_field(setup_result, "graph_id")
            agent_definition_data = get_result_field(
                setup_result, "agent_definition_data", {}
            )
            no_of_rows = get_result_field(setup_result, "no_of_rows", 20)
            custom_columns = get_result_field(setup_result, "custom_columns", [])
            property_list = get_result_field(setup_result, "property_list", [])
            transcripts = get_result_field(setup_result, "transcripts", {})
            custom_instruction = get_result_field(setup_result, "custom_instruction")
            mode = get_result_field(setup_result, "mode", "voice")

            # Step 2: Extract intents from transcripts or propose use cases
            intents_result = await workflow.execute_activity(
                "extract_intents_activity",
                ExtractIntentsInput(
                    scenario_id=input.scenario_id,
                    graph_id=graph_id,
                    agent_definition_data=agent_definition_data,
                    transcripts=transcripts,
                    no_of_rows=no_of_rows,
                ),
                start_to_close_timeout=timedelta(minutes=30),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=LLM_RETRY_POLICY,
            )

            if get_result_field(intents_result, "status") != "COMPLETED":
                error = get_result_field(
                    intents_result, "error", "Intent extraction failed"
                )
                return CreateGraphScenarioWorkflowOutput(
                    scenario_id=input.scenario_id,
                    status="FAILED",
                    error=error,
                )

            intent_dict = get_result_field(intents_result, "intent_dict", {})

            # Step 3: Process branches and create metadata
            branches_result = await workflow.execute_activity(
                "process_branches_activity",
                ProcessBranchesInput(
                    graph_id=graph_id,
                    agent_definition_data=agent_definition_data,
                    custom_instruction=custom_instruction,
                    no_of_rows=no_of_rows,
                    mode=mode,
                ),
                start_to_close_timeout=timedelta(hours=1),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=LLM_RETRY_POLICY,
            )

            if get_result_field(branches_result, "status") != "COMPLETED":
                error = get_result_field(
                    branches_result, "error", "Branch processing failed"
                )
                return CreateGraphScenarioWorkflowOutput(
                    scenario_id=input.scenario_id,
                    status="FAILED",
                    error=error,
                )

            branches_metadata = get_result_field(
                branches_result, "branches_metadata", []
            )
            branch_metadata_lookup = get_result_field(
                branches_result, "branch_metadata_lookup", {}
            )

            # Step 4: Generate cases - parallel fan-out for each intent
            all_cases = []

            if intent_dict:
                # Calculate batch sizes per intent
                num_intents = len(intent_dict)
                base_batch = no_of_rows // num_intents
                remainder = no_of_rows % num_intents
                num_branches = len(branches_metadata) if branches_metadata else 0

                # Create activity inputs for parallel execution
                intent_items = list(intent_dict.items())
                # Fire all intent activities in parallel via Temporal
                intent_handles = []
                for i, (intent_id, intent_value) in enumerate(intent_items):
                    batch_size = base_batch + (1 if i < remainder else 0)
                    if batch_size <= 0:
                        continue

                    # Assign a subset of branches for diversity (same as v3)
                    if num_branches > 0 and batch_size < num_branches:
                        branches_needed = min(batch_size, num_branches)
                        start_idx = i % num_branches
                        intent_branches = [
                            branches_metadata[(start_idx + j) % num_branches]
                            for j in range(branches_needed)
                        ]
                    else:
                        intent_branches = branches_metadata

                    handle = workflow.start_activity(
                        "generate_cases_for_intent_activity",
                        GenerateCasesForIntentInput(
                            intent_id=intent_id,
                            intent_value=intent_value,
                            branches_metadata=intent_branches,
                            agent_definition_data=agent_definition_data,
                            batch_size=batch_size,
                            property_list=property_list,
                            custom_columns=custom_columns,
                            mode=mode,
                            graph_id=graph_id,
                        ),
                        start_to_close_timeout=timedelta(hours=6),
                        heartbeat_timeout=timedelta(minutes=5),
                        retry_policy=LLM_RETRY_POLICY,
                    )
                    intent_handles.append(handle)

                # Await all handles
                for handle in intent_handles:
                    result = await handle
                    if get_result_field(result, "status") == "COMPLETED":
                        cases = get_result_field(result, "cases", [])
                        all_cases.extend(cases)
            else:
                # No intents - generate cases directly
                single_result = await workflow.execute_activity(
                    "generate_cases_for_intent_activity",
                    GenerateCasesForIntentInput(
                        intent_id="default",
                        intent_value="general",
                        branches_metadata=branches_metadata,
                        agent_definition_data=agent_definition_data,
                        batch_size=no_of_rows,
                        property_list=property_list,
                        custom_columns=custom_columns,
                        mode=mode,
                        graph_id=graph_id,
                    ),
                    start_to_close_timeout=timedelta(hours=6),
                    heartbeat_timeout=timedelta(minutes=5),
                    retry_policy=LLM_RETRY_POLICY,
                )

                if get_result_field(single_result, "status") == "COMPLETED":
                    all_cases = get_result_field(single_result, "cases", [])

            if not all_cases:
                return CreateGraphScenarioWorkflowOutput(
                    scenario_id=input.scenario_id,
                    status="FAILED",
                    error="No cases generated",
                )

            # Step 5: Categorize and validate cases
            validate_result = await workflow.execute_activity(
                "categorize_and_validate_activity",
                CategorizeAndValidateInput(
                    cases=all_cases,
                    branch_metadata_lookup=branch_metadata_lookup,
                    mode=mode,
                    custom_columns=custom_columns,
                ),
                start_to_close_timeout=timedelta(hours=1),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=LLM_RETRY_POLICY,
            )

            if get_result_field(validate_result, "status") != "COMPLETED":
                error = get_result_field(validate_result, "error", "Validation failed")
                return CreateGraphScenarioWorkflowOutput(
                    scenario_id=input.scenario_id,
                    status="FAILED",
                    error=error,
                )

            validated_cases = get_result_field(validate_result, "validated_cases", [])

            # Step 6: Create dataset and persist cases
            # Extract name and description from validated_data
            scenario_name = input.validated_data.get("name", "Graph Scenario")
            scenario_description = input.validated_data.get("description", "")

            dataset_result = await workflow.execute_activity(
                "create_scenario_dataset_activity",
                CreateScenarioDatasetInput(
                    scenario_id=input.scenario_id,
                    cases=validated_cases,
                    name=scenario_name,
                    description=scenario_description,
                    custom_columns=custom_columns,
                    agent_definition_data=agent_definition_data,
                ),
                start_to_close_timeout=timedelta(hours=1),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=SETUP_RETRY_POLICY,
            )

            if get_result_field(dataset_result, "status") != "COMPLETED":
                error = get_result_field(
                    dataset_result, "error", "Dataset creation failed"
                )
                return CreateGraphScenarioWorkflowOutput(
                    scenario_id=input.scenario_id,
                    status="FAILED",
                    error=error,
                )

            dataset_id = get_result_field(dataset_result, "dataset_id")

            # Step 7: Finalize scenario. add_persona_automatically=True drops
            # user-picked personas so finalize does not overwrite the empty
            # property_list intent.
            persona_ids = (
                []
                if input.validated_data.get("add_persona_automatically", False)
                else input.validated_data.get("personas", [])
            )
            finalize_result = await workflow.execute_activity(
                "finalize_graph_scenario_activity",
                FinalizeGraphScenarioInput(
                    scenario_id=input.scenario_id,
                    dataset_id=dataset_id,
                    persona_ids=(
                        [str(pid) for pid in persona_ids] if persona_ids else None
                    ),
                ),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=SETUP_RETRY_POLICY,
            )

            return CreateGraphScenarioWorkflowOutput(
                scenario_id=get_result_field(
                    finalize_result, "scenario_id", input.scenario_id
                ),
                dataset_id=get_result_field(finalize_result, "dataset_id"),
                status=get_result_field(finalize_result, "status", "COMPLETED"),
                error=get_result_field(finalize_result, "error"),
            )

        except Exception as e:
            # Re-raise to mark workflow as Failed in Temporal UI
            raise


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "ScenarioGenerationWorkflow",
    "AddScenarioRowsWorkflow",
    "AddScenarioColumnsWorkflow",
    "CreateDatasetScenarioWorkflow",
    "CreateScriptScenarioWorkflow",
    "CreateGraphScenarioWorkflow",
]
