import ast
import asyncio
import json
import math
import re
import copy
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple, Union
import pandas as pd
import random
from tfc.telemetry import wrap_for_async, wrap_for_thread
from simulate.models import (
    AgentDefinition,
    Scenarios,
)
from simulate.models.agent_definition import AgentTypeChoices
from ee.agenthub.scenario_graph.prompt import (
    UNIFIED_CATEGORY_PROMPT,
    SITUATION_NODE_GENERATION_PROMPT,
    BRANCH_DESCRIPTION_PROMPT,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.choices import (
    DataTypeChoices,
    DatasetSourceChoices,
    SourceChoices,
)
from ee.agenthub.synthetic_data_agent.synthetic_data_agent import (
    SyntheticDataAgent,
)
from simulate.models.scenario_graph import NodeType, ScenarioGraph
from ee.agenthub.scenario_graph.graph_generator import (
    ConversationGraphGenerator,
)
from ee.agenthub.scenario_graph.persona_configurator import (
    PersonaConfigurator,
)
from agentic_eval.core.llm.llm import LLM
try:
    from ee.usage.models.usage import APICallTypeChoices
except ImportError:
    APICallTypeChoices = None
try:
    from ee.usage.utils.usage_entries import count_text_tokens, log_and_deduct_cost_for_api_request
except ImportError:
    count_text_tokens = None
    log_and_deduct_cost_for_api_request = None
import structlog
from django.db import close_old_connections, transaction

logger = structlog.get_logger(__name__)

# SDA plan distribution constants (must match synthetic_data_agent.py)
# SDA creates plans using: num_plans = min(SDA_MAX_PLANS, ceil(batch_size / SDA_ROWS_PER_PLAN))
SDA_MAX_PLANS = 30
SDA_ROWS_PER_PLAN = 10

# Max parallel workers for branch processing (LLM calls)
MAX_BRANCH_WORKERS = 15


class EnhancedScenariosAgent:
    """
    Enhanced agent that generates GRAPH-type scenarios using a two-step process:

    1. First, create a detailed conversation graph from AgentDefinition
    2. Then, for each branch in the graph, generate persona, situation, and outcome data

    This approach ensures comprehensive coverage of all possible conversation flows
    while maintaining the detailed case generation for each specific path.
    """

    def __init__(
        self,
        agent_definition_id: str = None,
        no_of_rows: Optional[int] = 20,
        custom_columns: Optional[List[Dict]] = None,
        simulation_mode: str = None,
        agent_definition=None,
    ):
        """
        Initialize the EnhancedScenariosAgent.

        Args:
            agent_definition_id: UUID of an AgentDefinition record in the database.
                Used for SDK-based agent simulations where the agent config is stored in DB.
            no_of_rows: Number of scenario rows to generate. Defaults to 20.
            custom_columns: Optional list of custom column definitions.
            simulation_mode: Override mode ('voice' or 'chat'). If not provided,
                mode is determined from agent_type.
            agent_definition: Pre-built agent definition object (adapter pattern).
                Used for prompt-based scenarios where we create an in-memory adapter
                that mimics the AgentDefinition interface (agent_name, description,
                agent_type, languages, inbound, organization, workspace, etc.)
                without requiring an actual database record.

        Note:
            Either `agent_definition_id` OR `agent_definition` must be provided.
            - Use `agent_definition_id` for SDK-based agents (fetches from DB)
            - Use `agent_definition` for prompt-based scenarios (adapter object)

            The adapter pattern allows prompt templates to reuse the same graph
            generation and scenario creation logic without needing to create
            temporary AgentDefinition records in the database.
        """
        if agent_definition is not None:
            self.agent_definition = agent_definition
        elif agent_definition_id:
            self.agent_definition: AgentDefinition = (
                AgentDefinition.no_workspace_objects.get(id=agent_definition_id)
            )  # type: ignore[misc]
        else:
            raise ValueError(
                "Either agent_definition_id or agent_definition is required."
            )

        self.mode = (
            "voice"
            if getattr(self.agent_definition, "agent_type", AgentTypeChoices.VOICE)
            == AgentTypeChoices.VOICE
            else "chat"
        )
        if simulation_mode:
            self.mode = simulation_mode

        self.graph_generator = ConversationGraphGenerator(
            agent_definition_id=agent_definition_id,
            simulation_mode=simulation_mode,
            agent_definition=self.agent_definition,
        )
        self.no_of_rows = no_of_rows
        self.custom_columns = custom_columns or []
        self.custom_instruction: Optional[str] = None
        self.intent_dict: Optional[Dict[str, str]] = None
        self.configuration_snapshot: Optional[Dict] = None
        # Store LLM config for creating thread-local instances
        self._llm_config = {
            "model_name": "vertex_ai/gemini-2.5-pro",
            "temperature": 0.3,
            "max_tokens": 400,
            "provider": "vertex_ai",
            "api_key": None,
        }
        self.llm = LLM(**self._llm_config)

    def _create_thread_local_llm(self) -> LLM:
        """Create a new LLM instance for thread-safe parallel execution.

        The shared self.llm is not thread-safe due to mutable state
        (token_usage, cost) that gets updated during call_llm.
        """
        return LLM(**self._llm_config)

    # =========================================================================
    # Serialization methods for Temporal activity data passing
    # =========================================================================

    def serialize_agent_definition(self) -> Dict[str, Any]:
        """Serialize agent definition to a dict for passing between Temporal activities.

        Returns a dict that can be used to reconstruct the agent definition.
        """
        agent_def = self.agent_definition
        return {
            "id": str(getattr(agent_def, "id", "")),
            "agent_name": getattr(agent_def, "agent_name", ""),
            "description": getattr(agent_def, "description", ""),
            "agent_type": getattr(agent_def, "agent_type", "voice"),
            "languages": getattr(agent_def, "languages", ["en"]),
            "language": getattr(agent_def, "language", "en"),
            "inbound": getattr(agent_def, "inbound", True),
            "contact_number": getattr(agent_def, "contact_number", None),
            "organization_id": str(getattr(agent_def, "organization_id", ""))
            if hasattr(agent_def, "organization_id")
            else str(getattr(getattr(agent_def, "organization", None), "id", "")),
            "workspace_id": str(getattr(agent_def, "workspace_id", ""))
            if hasattr(agent_def, "workspace_id")
            else str(getattr(getattr(agent_def, "workspace", None), "id", "")),
        }

    @classmethod
    def from_serialized_agent_definition(
        cls,
        agent_definition_data: Dict[str, Any],
        no_of_rows: int = 20,
        custom_columns: Optional[List[Dict]] = None,
        simulation_mode: Optional[str] = None,
    ) -> "EnhancedScenariosAgent":
        """Create an EnhancedScenariosAgent from serialized agent definition data.

        Args:
            agent_definition_data: Dict from serialize_agent_definition()
            no_of_rows: Number of rows to generate
            custom_columns: Custom column definitions
            simulation_mode: Override mode ('voice' or 'chat')

        Returns:
            EnhancedScenariosAgent instance
        """
        import types

        from accounts.models import Organization, Workspace

        # Reconstruct organization and workspace from IDs
        organization = None
        workspace = None
        org_id = agent_definition_data.get("organization_id")
        ws_id = agent_definition_data.get("workspace_id")

        if org_id:
            try:
                organization = Organization.objects.get(id=org_id)
            except Exception:
                pass
        if ws_id:
            try:
                workspace = Workspace.objects.get(id=ws_id)
            except Exception:
                pass

        # Create a SimpleNamespace adapter that mimics AgentDefinition
        agent_definition = types.SimpleNamespace(
            id=agent_definition_data.get("id", ""),
            agent_name=agent_definition_data.get("agent_name", ""),
            description=agent_definition_data.get("description", ""),
            agent_type=agent_definition_data.get("agent_type", "voice"),
            languages=agent_definition_data.get("languages", ["en"]),
            language=agent_definition_data.get("language", "en"),
            inbound=agent_definition_data.get("inbound", True),
            contact_number=agent_definition_data.get("contact_number"),
            organization=organization,
            workspace=workspace,
        )

        return cls(
            no_of_rows=no_of_rows,
            custom_columns=custom_columns,
            simulation_mode=simulation_mode,
            agent_definition=agent_definition,
        )

    # =========================================================================
    # Methods for Temporal sub-activities (v2 multi-activity workflow)
    # =========================================================================

    def process_branches(
        self,
        graph_id: str,
        custom_instruction: Optional[str] = None,
    ) -> Tuple[List[Dict], Dict[str, Dict]]:
        """Process branches from graph and return metadata.

        This is Step 3 in the Temporal workflow - extract and process branches.

        Args:
            graph_id: ID of the ScenarioGraph to extract branches from
            custom_instruction: Optional instruction for filtering branches

        Returns:
            Tuple of (branches_metadata, branch_metadata_lookup)
            - branches_metadata: List of processed branch metadata dicts
            - branch_metadata_lookup: Dict mapping branch_name to metadata
        """
        self.custom_instruction = custom_instruction

        # Get branches from graph
        branches = self.graph_generator.get_branches(graph_id=graph_id)
        logger.info(f"Found {len(branches)} conversation branches")

        if not branches:
            return [], {}

        needed_branches = min(self.no_of_rows, len(branches))

        # Process branches in parallel
        detailed_branches: List[Dict] = []
        detailed_branches_metadata: List[Dict] = []

        num_workers = min(len(branches), MAX_BRANCH_WORKERS)
        logger.info(
            f"Processing {len(branches)} branches with {num_workers} parallel workers"
        )

        wrapped_process_branch = wrap_for_thread(self._process_branch)

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            future_to_idx = {
                executor.submit(wrapped_process_branch, branch, graph_id): idx
                for idx, branch in enumerate(branches)
            }

            results: List[Optional[Tuple[Dict, Dict]]] = [None] * len(branches)
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    logger.exception(f"Error processing branch {idx}: {e}")
                    results[idx] = None

        # Unpack results
        for result in results:
            if result:
                detailed_branch, branch_metadata = result
                detailed_branches.append(detailed_branch)
                detailed_branches_metadata.append(branch_metadata)

        # Filter/select branches
        if custom_instruction:
            detailed_branches, detailed_branches_metadata = (
                self._filter_branches_with_llm(
                    detailed_branches, detailed_branches_metadata, needed_branches
                )
            )
        else:
            detailed_branches, detailed_branches_metadata = self._select_branches(
                detailed_branches,
                detailed_branches_metadata,
                needed_branches,
                strategy="sample",
            )

        # Create lookup
        branch_metadata_lookup: Dict[str, Dict] = {
            md["branch_name"]: md for md in detailed_branches_metadata
        }

        # Add branch_metadata_payloads to each metadata for SDA
        branch_metadata_payloads = self._create_branch_metadata_strings(
            detailed_branches_metadata
        )
        for i, payload in enumerate(branch_metadata_payloads):
            if i < len(detailed_branches_metadata):
                detailed_branches_metadata[i]["metadata_payload"] = payload

        logger.info(f"Processed {len(detailed_branches_metadata)} branches")
        return detailed_branches_metadata, branch_metadata_lookup

    def generate_cases_for_single_intent(
        self,
        intent_id: str,
        intent_value: str,
        branches_metadata: List[Dict],
        batch_size: int,
        property_list: Optional[List[Dict]] = None,
        graph_id: Optional[str] = None,
    ) -> List[Dict]:
        """Generate cases for a single intent.

        This is Step 4 in the Temporal workflow - called in parallel for each intent.

        Args:
            intent_id: Unique identifier for this intent
            intent_value: The intent description/value
            branches_metadata: List of branch metadata from process_branches()
            batch_size: Number of cases to generate for this intent
            property_list: Optional persona property constraints
            graph_id: Optional graph ID for context

        Returns:
            List of generated cases with intent_id added
        """
        logger.info(f"Generating {batch_size} cases for intent: {intent_value}")

        # Set custom instruction for this intent
        self.custom_instruction = f"The user intent is: {intent_value}."

        try:
            sda = SyntheticDataAgent(simulation_mode=self.mode)

            if not branches_metadata:
                logger.warning("No branches metadata provided")
                return []

            # Build branch metadata payloads for SDA
            branch_metadata_payloads = []
            for md in branches_metadata:
                if "metadata_payload" in md:
                    branch_metadata_payloads.append(md["metadata_payload"])
                else:
                    # Create payload on the fly
                    payload = {
                        "branch_name": md.get("branch_name", "unknown"),
                        "metadata_string": self._create_single_branch_metadata_string(
                            md
                        ),
                    }
                    branch_metadata_payloads.append(payload)

            # Use first branch as template
            template_branch = {
                "detailedPath": branches_metadata[0].get("detailedPath", []),
                "path": branches_metadata[0].get("path", []),
                "start_node": branches_metadata[0].get("start_node", ""),
                "end_node": branches_metadata[0].get("end_node", ""),
            }

            # Generate raw cases
            raw_cases = self._generate_raw_cases_from_sda(
                template_branch,
                sda,
                {},  # user_requirements
                batch_size,
                property_list,
                branch_metadata_payloads,
                self.mode,
            )

            if raw_cases:
                # Add intent_id to each case
                for case in raw_cases:
                    case["intent_id"] = intent_id

                logger.info(
                    f"Generated {len(raw_cases)} cases for intent: {intent_value}"
                )
                return raw_cases

            return []

        except Exception as e:
            logger.exception(f"Error generating cases for intent '{intent_value}': {e}")
            return []

    def _create_single_branch_metadata_string(self, metadata: Dict) -> str:
        """Create a single branch metadata string for SDA."""
        branch_name = metadata.get("branch_name", "unknown")
        metadata_lines = [
            "Conversation Branch Information:",
            f"- Branch Name: {branch_name}",
            f"- Description: {metadata.get('description', '')}",
            f"- Start Node: {metadata.get('start_node', 'unknown')}",
            f"- End Node: {metadata.get('end_node', 'unknown')}",
            f"- Conversation Flow: {metadata.get('conversation_flow', '')}",
        ]
        return "\n".join(metadata_lines)

    def categorize_and_validate_cases(
        self,
        cases: List[Dict],
        branch_metadata_lookup: Dict[str, Dict],
    ) -> List[Dict]:
        """Categorize branches and validate personas in cases.

        This is Step 5 in the Temporal workflow.

        Args:
            cases: List of raw cases from generate_cases_for_single_intent()
            branch_metadata_lookup: Dict mapping branch_name to metadata

        Returns:
            List of validated and enriched cases
        """
        if not cases:
            return []

        logger.info(f"Categorizing and validating {len(cases)} cases")

        # Build branch -> situations mapping for categorization
        # Filter out None/NaN branch names to prevent None keys in dict
        branch_to_situations: Dict[str, List[str]] = {}
        for case in cases:
            branch_name = case.get("conversation_branch", "")
            situation = case.get("situation", "")
            # Skip None or NaN branch names
            if branch_name and not (
                isinstance(branch_name, float) and math.isnan(branch_name)
            ):
                if branch_name not in branch_to_situations:
                    branch_to_situations[branch_name] = []
                if situation:
                    branch_to_situations[branch_name].append(situation)

        # Parallelize branch categorization
        branch_to_category: Dict[str, str] = {}
        if branch_to_situations:
            num_category_workers = min(len(branch_to_situations), MAX_BRANCH_WORKERS)
            logger.info(
                f"Categorizing {len(branch_to_situations)} branches with "
                f"{num_category_workers} parallel workers"
            )

            wrapped_categorize_branch = wrap_for_thread(self._categorize_branch)

            with ThreadPoolExecutor(max_workers=num_category_workers) as executor:
                futures = [
                    executor.submit(wrapped_categorize_branch, branch, situations)
                    for branch, situations in branch_to_situations.items()
                ]

                for future in as_completed(futures):
                    try:
                        branch, category = future.result()
                        # Only add if branch is not None
                        if branch is not None:
                            branch_to_category[branch] = category
                    except Exception as e:
                        logger.exception(f"Error in categorization future: {e}")

        # Apply categories to cases
        for case in cases:
            branch_name = case.get("conversation_branch", "")
            if branch_name in branch_to_category:
                case["branch_category"] = branch_to_category[branch_name]

        # Validate personas
        required_fields = PersonaConfigurator.get_required_fields(self.mode)
        property_dict = PersonaConfigurator.get_property_dict(self.mode)

        for case in cases:
            persona = case.get("persona", {})
            if isinstance(persona, str):
                try:
                    persona = json.loads(persona)
                    persona = {k.lower(): v for k, v in persona.items()}
                except Exception:
                    persona = {}
            elif not isinstance(persona, dict):
                persona = {}

            # Fill missing required fields
            for field in required_fields:
                if field not in persona or not persona[field]:
                    if field in property_dict:
                        prop_value = property_dict[field]
                        if isinstance(prop_value, list):
                            persona[field] = random.choice(prop_value)
                        else:
                            persona[field] = prop_value
                    else:
                        persona[field] = "Not Specified"

            # Keep only required fields in order
            persona = {k: persona[k] for k in required_fields if k in persona}
            case["persona"] = persona

        # Enrich with branch data
        enriched_cases = self._enrich_cases_with_branch_data(
            cases, branch_metadata_lookup
        )

        logger.info(f"Validated and enriched {len(enriched_cases)} cases")
        return enriched_cases

    def create_scenario_dataset_from_cases(
        self,
        scenario_id: str,
        cases: List[Dict],
        name: str,
        description: str,
    ) -> Dataset:
        """Create a Dataset from validated cases.

        This is Step 6 in the Temporal workflow.

        Args:
            scenario_id: ID of the parent scenario
            cases: Validated cases from categorize_and_validate_cases()
            name: Dataset name
            description: Dataset description

        Returns:
            Created Dataset instance
        """
        scenario = Scenarios.objects.get(id=scenario_id)
        return self._create_scenario_dataset(scenario, cases, name, description)

    def run(
        self,
        name: str,
        description: str = "",
        user_requirements: Optional[Dict] = None,
        graph_id: Optional[str] = None,
        property_list: Optional[List[Dict]] = None,
        mode: Optional[str] = None,
        intent_dict: Optional[Dict[str, str]] = None,
    ) -> Tuple[Scenarios, Dataset]:
        """
        Create a GRAPH scenario using an existing graph from the database.

        Pipeline:
        1) Load existing conversation graph from database using graph_id
        2) Extract all possible conversation branches from the graph
        3) For each branch, use SDA agent to generate persona, situation, and outcome data
        4) Create individual cases with complete conversation flows
        5) Store all data in Dataset table with scenario-specific columns
        6) Create unified graph structure from all cases
        7) Store unified graph in ScenarioGraph model
        """
        # mode = "chat"
        scenario_graph: ScenarioGraph = ScenarioGraph.no_workspace_objects.get(
            id=graph_id
        )  # type: ignore[misc]
        scenario = scenario_graph.scenario

        # Extract optional metadata from scenario if available
        if scenario and scenario.metadata:
            metadata = scenario.metadata
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}
            self.custom_instruction = metadata.get("custom_instruction")
            self.intent_dict = intent_dict
            # Attempt to load configuration_snapshot using agent_definition_version_id if present
            agent_definition_version_id = metadata.get("agent_definition_version_id")
            if agent_definition_version_id:
                try:
                    from simulate.models.agent_version import AgentVersion

                    version = AgentVersion.objects.filter(
                        id=agent_definition_version_id
                    ).first()
                    if version:
                        self.configuration_snapshot = version.configuration_snapshot
                except Exception:
                    self.configuration_snapshot = None

        # Step 1: Load existing conversation graph from database
        logger.info("Step 1: Loading existing conversation graph from database...")
        if not graph_id:
            raise ValueError("graph_id is required to load existing graph")

        # Get graph data from database
        graph_data = self.graph_generator._get_graph_data_from_database(graph_id)
        if not graph_data:
            raise ValueError(f"No graph data found for graph_id: {graph_id}")

        # Step 2: Extract all conversation branches from existing graph
        logger.info("Step 2: Extracting conversation branches from existing graph...")
        branches = self.graph_generator.get_branches(graph_id=graph_id)
        logger.info(f"Found {len(branches)} conversation branches")
        # Step 3: Generate cases for each branch
        # Use provided mode or default to self.mode
        effective_mode = mode or self.mode
        logger.info("Step 3: Generating cases for each branch...")
        if self.intent_dict:
            cases: List[Dict] = []
            logger.info(
                f"Using intent dict with {len(self.intent_dict)} intents for case generation"
            )
            # Parallelize case generation per intent to match other parallel patterns in this file
            cases: List[Dict] = []
            if self.intent_dict:
                logger.info(
                    f"Generating cases for {len(self.intent_dict)} intents in parallel"
                )
                num_intents = len(self.intent_dict)
                intent_batch_sizes = [self.no_of_rows // num_intents] * num_intents
                for i in range(self.no_of_rows % num_intents):
                    intent_batch_sizes[i] += 1

                def _intent_worker(
                    uid: str, intent: str, batch_size: int = 0
                ) -> List[Dict]:
                    try:
                        # thread-safety: close old DB connections for this thread
                        close_old_connections()
                        # Create a separate agent instance per thread to avoid concurrent mutation of self
                        agent_copy = EnhancedScenariosAgent(
                            no_of_rows=batch_size,
                            custom_columns=self.custom_columns,
                            simulation_mode=effective_mode,
                            agent_definition=self.agent_definition,
                        )
                        # preserve configuration snapshot if available
                        agent_copy.configuration_snapshot = self.configuration_snapshot
                        # set intent-specific custom instruction
                        agent_copy.custom_instruction = f"The user intent is: {intent}."
                        logger.info(f"Starting generation for intent: {intent}")
                        generated = agent_copy._generate_cases_for_branches(
                            branches,
                            user_requirements or {},
                            graph_id,
                            property_list,
                            effective_mode,
                        )
                        logger.info(
                            f"Completed generation for intent: {intent} -> {len(generated)} cases"
                        )
                        # add uid to each case
                        for n, case in enumerate(generated):
                            generated[n]["intent_id"] = uid
                        return generated
                    except Exception as e:
                        logger.exception(
                            f"Error generating cases for intent '{intent}': {e}"
                        )
                        return []

                num_workers = min(len(self.intent_dict), MAX_BRANCH_WORKERS)

                # Wrap function with OTel context propagation for thread safety
                wrapped_intent_worker = wrap_for_thread(_intent_worker)

                with ThreadPoolExecutor(max_workers=num_workers) as executor:
                    futures = {
                        executor.submit(
                            wrapped_intent_worker,
                            key,
                            val,
                            batch_size,
                        ): key
                        for (key, val), batch_size in zip(
                            list(self.intent_dict.items()), intent_batch_sizes
                        )
                    }
                    for future in as_completed(futures):
                        try:
                            res = future.result()
                            if res:
                                cases.extend(res)
                        except Exception as e:
                            logger.exception(f"Intent future exception: {e}")

        else:
            logger.info("No intent list provided for case generation")
            cases = self._generate_cases_for_branches(
                branches,
                user_requirements or {},
                graph_id,
                property_list,
                effective_mode,
            )
        logger.info(f"Generated {len(cases)} cases")

        # Step 4: Create Dataset with scenario data
        logger.info("Step 4: Creating scenario dataset...")
        dataset = self._create_scenario_dataset(scenario, cases, name, description)

        return scenario, dataset

    def _select_branches(
        self,
        detailed_branches: List[Dict],
        detailed_branches_metadata: List[Dict],
        needed_branches: int,
        strategy: str = "keep_all",
        no_of_rows: Optional[int] = None,
    ) -> Tuple[List[Dict], List[Dict]]:
        """Select branches using the specified strategy.

        Args:
            detailed_branches: List of detailed branch dicts
            detailed_branches_metadata: Corresponding metadata for each branch
            needed_branches: Number of branches needed (used only for 'sample' strategy)
            strategy: Branch selection strategy
                - "keep_all": Keep ALL branches, distribute rows evenly (default)
                - "sample": Randomly sample `needed_branches` branches
            no_of_rows: Number of rows to generate (unused, kept for API compatibility)

        Returns:
            Tuple of (selected_branches, selected_metadata)
        """
        num_branches = len(detailed_branches)

        # Limit branches to SDA_MAX_PLANS since that's the maximum plans SDA can create
        # Each plan uses one branch, so having more branches than max plans is wasteful
        # if num_branches > SDA_MAX_PLANS:
        #     logger.info(
        #         f"More branches ({num_branches}) than SDA max plans ({SDA_MAX_PLANS}). "
        #         f"Randomly sampling {SDA_MAX_PLANS} branches."
        #     )
        #     sample_indices = random.sample(range(num_branches), SDA_MAX_PLANS)
        #     return (
        #         [detailed_branches[i] for i in sample_indices],
        #         [detailed_branches_metadata[i] for i in sample_indices],
        #     )

        if strategy == "sample" and needed_branches < num_branches:
            # Option B: Random sampling of branches
            sample_indices = random.sample(range(num_branches), needed_branches)
            return (
                [detailed_branches[i] for i in sample_indices],
                [detailed_branches_metadata[i] for i in sample_indices],
            )
        # Option A (default): Keep all branches, SDA distributes rows evenly
        return detailed_branches, detailed_branches_metadata

    def _filter_branches_with_llm(
        self,
        detailed_branches: List[Dict],
        detailed_branches_metadata: List[Dict],
        needed_branches: int,
    ) -> Tuple[List[Dict], List[Dict]]:
        """Use LLM to filter branches based on custom instruction."""
        try:
            # Build prompt with indexed branches
            prompt_lines = [
                "You are given a list of conversation branch metadata objects with indices.",
                "User instruction:",
                f"{self.custom_instruction}",
                "",
                "For each branch, decide whether it should be KEPT to satisfy the user instruction.",
                "Return a JSON array of indices (0-based) of branches to KEEP and nothing else. Example: [0,2,5]",
                "",
                "Branches:",
            ]

            # Add branches WITH indices

            stringified_branch_metadata: Dict[int, str] = {}
            for idx, md in enumerate(detailed_branches_metadata):
                try:
                    stringified_branch_metadata[idx] = json.dumps(
                        md, ensure_ascii=False
                    )
                except Exception:
                    stringified_branch_metadata[idx] = str(md)

            # Add a single entry containing the dict of indexed branch metadata to the prompt

            prompt_lines.append(
                json.dumps(stringified_branch_metadata, ensure_ascii=False)
            )

            messages = [
                {
                    "role": "system",
                    "content": "You are an assistant that filters items based on instructions.",
                },
                {"role": "user", "content": "\n".join(prompt_lines)},
            ]
            thread_llm = self._create_thread_local_llm()

            resp = thread_llm._get_completion_content(messages)

            resp_text = resp.strip()

            # Parse response
            kept_indices: List[int] = []
            try:
                parsed = json.loads(resp_text)
                if isinstance(parsed, list):
                    kept_indices = [
                        int(x)
                        for x in parsed
                        if isinstance(x, (int, float))
                        or (isinstance(x, str) and x.isdigit())
                    ]
            except json.JSONDecodeError:
                # Fallback: extract numbers from text
                nums = re.findall(r"\d+", resp_text)
                kept_indices = [int(x) for x in nums]

            # Validate indices
            kept_indices = [i for i in kept_indices if 0 <= i < len(detailed_branches)]

            if kept_indices:
                # Limit to needed_branches
                if len(kept_indices) > needed_branches:
                    kept_indices = kept_indices[:needed_branches]

                filtered_branches = [detailed_branches[i] for i in kept_indices]
                filtered_metadata = [
                    detailed_branches_metadata[i] for i in kept_indices
                ]

                # Limit to SDA_MAX_PLANS since that's the maximum plans SDA can create
                # if len(filtered_branches) > SDA_MAX_PLANS:
                #     logger.info(
                #         f"After LLM filtering: {len(filtered_branches)} branches > {SDA_MAX_PLANS} max plans. "
                #         f"Randomly sampling {SDA_MAX_PLANS} branches."
                #     )
                #     sample_indices = random.sample(range(len(filtered_branches)), SDA_MAX_PLANS)
                #     return (
                #         [filtered_branches[i] for i in sample_indices],
                #         [filtered_metadata[i] for i in sample_indices],
                #     )

                return filtered_branches, filtered_metadata
            else:
                logger.warning(
                    "LLM returned no valid indices, falling back to keep_all strategy"
                )
                return self._select_branches(
                    detailed_branches,
                    detailed_branches_metadata,
                    needed_branches,
                    strategy="sample",
                )

        except Exception as e:
            logger.exception(f"Error filtering branches with LLM: {e}")
            return self._select_branches(
                detailed_branches,
                detailed_branches_metadata,
                needed_branches,
                strategy="sample",
            )

    def _process_branch(self, branch: Dict, graph_id: str) -> Tuple[Dict, Dict]:
        """Process a single branch and return (detailed_branch, metadata).

        This method is designed to be called in parallel via ThreadPoolExecutor.
        Thread-safety: Closes old Django connections and uses thread-local LLM.
        """
        # Close old Django connections for this thread (thread-safety)
        close_old_connections()

        detailed_branch = self.graph_generator.get_branch_with_messages_and_prompts(
            branch, graph_id
        )
        # Create thread-local LLM instance for generating branch description
        # (shared self.llm is not thread-safe due to mutable token_usage/cost)
        branch_metadata = self._create_branch_metadata_dict(
            detailed_branch, use_thread_local_llm=True
        )
        return detailed_branch, branch_metadata

    def _categorize_branch(self, branch: str, situations: List[str]) -> Tuple[str, str]:
        """Categorize a single branch using LLM. Returns (branch_name, category).

        This method is designed to be called in parallel via ThreadPoolExecutor.
        Thread-safety: Creates thread-local LLM instance.
        """
        try:
            # Create thread-local LLM (shared self.llm is not thread-safe)
            thread_llm = self._create_thread_local_llm()
            prompt = UNIFIED_CATEGORY_PROMPT.format(
                branch=branch,
                situations=situations,
            )
            messages = [{"role": "user", "content": prompt}]
            response = thread_llm._get_completion_content(messages)
            category = response.strip()
            logger.debug(f"Generated branch category for branch {branch}: {category}")
            return branch, category
        except Exception as e:
            logger.warning(f"Error generating branch category for {branch}: {e}")
            return branch, "miscellaneous"

    def _generate_cases_for_branches(
        self,
        branches: List[Dict],
        user_requirements: Dict,
        graph_id: Optional[str] = None,
        property_list: Optional[List[Dict]] = None,
        mode: str = "voice",
    ) -> List[Dict]:
        """Generate cases for each conversation branch using SDA agent."""
        cases: List[Dict] = []

        try:
            sda = SyntheticDataAgent(simulation_mode=mode)
            # if total_rows is specified, divide equally among branches and total rows greater than number of branches
            # if total_rows and total_rows > len(branches):
            #     rows_per_branch = total_rows // len(branches)
            # elif total_rows and total_rows <= len(branches):
            #     # randomly pick branches equal to total_rows
            #     branches = branches[:total_rows]
            #     rows_per_branch = 1
            # # if total_rows is not specified, use 1
            # else:
            #     rows_per_branch = 1

            # randomly sample branches to number of plans calc in synth agent, i.e. rows/10
            # needed_branches = math.ceil(self.no_of_rows / 10)
            needed_branches = min(self.no_of_rows, len(branches))

            # Get detailed branches with messages and prompts (parallelized)
            detailed_branches: List[Dict] = []
            detailed_branches_metadata: List[Dict] = []

            # Parallelize branch processing - each branch makes LLM calls for description
            num_workers = min(len(branches), MAX_BRANCH_WORKERS)
            logger.info(
                f"Processing {len(branches)} branches with {num_workers} parallel workers"
            )

            # Wrap function with OTel context propagation for thread safety
            wrapped_process_branch = wrap_for_thread(self._process_branch)

            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                # Submit all branch processing tasks
                future_to_idx = {
                    executor.submit(wrapped_process_branch, branch, graph_id): idx
                    for idx, branch in enumerate(branches)
                }

                # Collect results maintaining original order
                results: List[Optional[Tuple[Dict, Dict]]] = [None] * len(branches)
                for future in as_completed(future_to_idx):
                    idx = future_to_idx[future]
                    try:
                        results[idx] = future.result()
                    except Exception as e:
                        logger.exception(f"Error processing branch {idx}: {e}")
                        results[idx] = None

            # Unpack results (filter out failures)
            for result in results:
                if result:
                    detailed_branch, branch_metadata = result
                    detailed_branches.append(detailed_branch)
                    detailed_branches_metadata.append(branch_metadata)

            # Select branches: LLM filtering if custom_instruction, otherwise keep all (default)
            if self.custom_instruction:
                detailed_branches, detailed_branches_metadata = (
                    self._filter_branches_with_llm(
                        detailed_branches, detailed_branches_metadata, needed_branches
                    )
                )
            # else:
            #     # Default: keep_all - SDA distributes rows evenly across ALL branches
            #     # Use strategy="sample" if you want to randomly sample needed_branches instead
            #     # Pass no_of_rows to handle edge case where branches > requested rows
            #     detailed_branches, detailed_branches_metadata = self._select_branches(
            #         detailed_branches, detailed_branches_metadata, needed_branches,
            #         strategy="keep_all", no_of_rows=self.no_of_rows
            #     )
            else:
                detailed_branches, detailed_branches_metadata = self._select_branches(
                    detailed_branches,
                    detailed_branches_metadata,
                    needed_branches,
                    strategy="sample",
                )

            if not detailed_branches:
                logger.warning("No detailed branches available for case generation")
                return cases

            branch_metadata_payloads = self._create_branch_metadata_strings(
                detailed_branches_metadata
            )
            logger.info(
                f"Processing {len(detailed_branches_metadata)} detailed branches..."
            )

            # Create lookup for enriching cases later
            branch_metadata_lookup: Dict[str, Dict] = {
                md["branch_name"]: md for md in detailed_branches_metadata
            }

            # Generate raw cases - SDA distributes rows across all branches
            template_branch = detailed_branches[0]
            raw_cases = self._generate_raw_cases_from_sda(
                template_branch,
                sda,
                user_requirements,
                self.no_of_rows,
                property_list,
                branch_metadata_payloads,
                mode,
            )

            if raw_cases:
                # Enrich each case with its branch's detailed_path
                enriched_cases = self._enrich_cases_with_branch_data(
                    raw_cases, branch_metadata_lookup
                )
                cases.extend(enriched_cases)

            logger.debug(f"Generated {len(cases)} cases")

        except Exception as e:
            logger.exception(f"Error generating cases for branches: {e}")
            # Fallback to simple cases
            cases = self._create_fallback_cases(
                branches[:3]
            )  # Limit to 3 fallback cases

        return cases

    def _sda_worker(
        self,
        payload_template: Dict,
        branch_meta: Dict,
        batch_size: int,
        prop_choice: Optional[Dict],
        mode: str = "voice",
    ):
        try:
            # thread-safety: close old DB connections
            close_old_connections()
            # create thread-local SDA to avoid shared mutable state
            local_sda = SyntheticDataAgent(simulation_mode=mode)
            local_payload = copy.deepcopy(payload_template)
            local_payload["batch_size"] = int(batch_size)
            # Round-robin assign property_list entry if available
            if prop_choice:
                # property_list expected to be list of property dicts
                local_payload["property_list"] = [prop_choice]

            return local_sda.generate_and_validate(
                local_payload,
                branch_metadatas=[branch_meta],
                called_for="simulate",
            )
        except Exception as e:
            logger.exception(
                f"Error in SDA worker for branch {branch_meta.get('branch_name')}: {e}"
            )
            return None

    async def _sda_worker_async(
        self,
        payload_template: Dict,
        branch_meta: Dict,
        batch_size: int,
        prop_choice: Optional[Dict],
        mode: str = "voice",
    ):
        try:
            close_old_connections()
            local_sda = SyntheticDataAgent(simulation_mode=mode)
            local_payload = copy.deepcopy(payload_template)
            local_payload["batch_size"] = int(batch_size)
            if prop_choice:
                local_payload["property_list"] = [prop_choice]

            return await local_sda._generate_and_validate_async(
                local_payload,
                branch_metadatas=[branch_meta],
                called_for="simulate",
            )
        except Exception as e:
            logger.exception(
                f"Error in async SDA worker for branch {branch_meta.get('branch_name')}: {e}"
            )
            return None

    def _generate_raw_cases_from_sda(
        self,
        template_branch: Dict,
        sda: SyntheticDataAgent,
        user_requirements: Dict,
        rows: int,
        property_list: Optional[List[Dict]] = None,
        branch_metadata_payloads: Optional[List[Dict]] = None,
        mode: str = "voice",
    ) -> Optional[List[Dict]]:
        """Generate raw cases using SDA, distributed across all branches.

        Args:
            template_branch: Template branch for SDA payload structure
            sda: SyntheticDataAgent instance
            user_requirements: User-provided requirements
            rows: Number of rows to generate
            property_list: Optional persona property constraints
            branch_metadata_payloads: Metadata for all branches (SDA distributes rows across these)

        Returns a list of raw case dicts (not yet enriched with detailed_path), or None on error.
        """
        # Pre-check usage before the LLM call
        try:
            from ee.usage.models.usage import APICallTypeChoices
        except ImportError:
            APICallTypeChoices = None
        try:
            from ee.usage.services.metering import check_usage
        except ImportError:
            check_usage = None

        usage_check = check_usage(
            str(self.agent_definition.organization.id),
            APICallTypeChoices.SYNTHETIC_DATA_GENERATION.value,
        )
        if not usage_check.allowed:
            raise ValueError(usage_check.reason or "Usage limit exceeded")

        try:
            # Validate template branch has a path
            if not template_branch.get("detailedPath"):
                logger.warning("No detailed path found in template branch")
                return None

            # Calculate effective batch_size to ensure enough SDA plans for branch diversity
            # SDA creates: num_plans = min(SDA_MAX_PLANS, ceil(batch_size / SDA_ROWS_PER_PLAN))
            # To use N branches, we need N plans, so batch_size >= N * SDA_ROWS_PER_PLAN
            num_branches = (
                len(branch_metadata_payloads) if branch_metadata_payloads else 1
            )
            min_batch_for_branches = num_branches * SDA_ROWS_PER_PLAN
            effective_batch_size = rows

            # create a list of batch size which is total rows by number of branches
            batch_sizes = [effective_batch_size // num_branches] * num_branches
            for i in range(effective_batch_size % num_branches):
                batch_sizes[i] += 1

            logger.info(
                f"Generating with effective_batch_size={effective_batch_size} "
                f"(rows={rows}, branches={num_branches}) to ensure branch diversity"
            )

            # Create SDA payload using the standard format with branch information
            sda_payload = self._create_branch_sda_payload(
                template_branch,
                user_requirements,
                effective_batch_size,  # Use effective batch size for plan diversity
                property_list,
                mode,
            )

            property_list_updated = sda_payload.get("property_list", [])

            if num_branches == 1:
                generated_data = sda.generate_and_validate(
                    sda_payload,
                    branch_metadatas=branch_metadata_payloads,
                    called_for="simulate",
                )

            else:
                try:
                    results = []
                    num_workers = min(num_branches, MAX_BRANCH_WORKERS)

                    # Wrap async function with OTel context propagation
                    wrapped_sda_worker = wrap_for_async(self._sda_worker_async)

                    async def run_workers():
                        semaphore = asyncio.Semaphore(num_workers)

                        async def run_one(i: int, branch_meta: Dict):
                            bs = (
                                batch_sizes[i]
                                if i < len(batch_sizes)
                                else batch_sizes[0]
                            )
                            if bs <= 0:
                                return None  # Skip branches with no rows allocated
                            prop_choice = None
                            if property_list_updated:
                                prop_choice = property_list_updated[
                                    i % len(property_list_updated)
                                ]
                            async with semaphore:
                                return await wrapped_sda_worker(
                                    sda_payload,
                                    branch_meta,
                                    bs,
                                    prop_choice,
                                    mode,
                                )

                        tasks = [
                            asyncio.create_task(run_one(i, branch_meta))
                            for i, branch_meta in enumerate(branch_metadata_payloads)
                        ]
                        return await asyncio.gather(*tasks)

                    results = [r for r in asyncio.run(run_workers()) if r is not None]
                    if not results:
                        generated_data = None
                    else:
                        # If pandas available and results are DataFrames or lists/dicts, concat into DataFrame

                        dfs = []
                        for r in results:
                            if hasattr(r, "columns") and hasattr(r, "to_dict"):
                                dfs.append(r)
                        if dfs:
                            try:
                                generated_data = pd.concat(dfs, ignore_index=True)
                            except Exception:
                                # fallback to first non-none result
                                generated_data = results[0]
                        else:
                            generated_data = results[0]
                except Exception as e:
                    logger.exception(f"Error in parallel SDA execution: {e}")
                    # Generate data using SDA agent
                    generated_data = sda.generate_and_validate(
                        sda_payload,
                        branch_metadatas=branch_metadata_payloads,
                        called_for="simulate",
                    )

            # check if 'branch_name' column is present in generated_data
            if "branch_name" in generated_data.columns:
                # 1. Build a dict: branch -> list of situations
                # Filter out None/NaN branch names to prevent None keys in dict
                branch_to_situations_raw = (
                    generated_data.groupby("branch_name")["situation"]
                    .apply(list)
                    .to_dict()
                )
                # Remove None key if present (can happen with NaN branch names)
                branch_to_situations = {
                    k: v
                    for k, v in branch_to_situations_raw.items()
                    if k is not None and (not isinstance(k, float) or not math.isnan(k))
                }

                # 2. Parallelize branch categorization - each branch makes an LLM call
                branch_to_category = {}
                if branch_to_situations:
                    num_category_workers = min(
                        len(branch_to_situations), MAX_BRANCH_WORKERS
                    )
                    logger.info(
                        f"Categorizing {len(branch_to_situations)} branches with {num_category_workers} parallel workers"
                    )

                    # Wrap function with OTel context propagation for thread safety
                    wrapped_categorize_branch = wrap_for_thread(self._categorize_branch)

                    with ThreadPoolExecutor(
                        max_workers=num_category_workers
                    ) as executor:
                        futures = [
                            executor.submit(
                                wrapped_categorize_branch, branch, situations
                            )
                            for branch, situations in branch_to_situations.items()
                        ]

                        for future in as_completed(futures):
                            try:
                                branch, category = future.result()
                                if branch is not None:
                                    branch_to_category[branch] = category
                            except Exception as e:
                                logger.exception(f"Error in categorization future: {e}")

                # 3. Map categories back onto the dataframe in one shot
                # Use fillna to replace NaN with empty string for unmapped values
                generated_data["branch_category"] = (
                    generated_data["branch_name"].map(branch_to_category).fillna("")
                )

            # Access persona column and validate/fill required fields
            persona_column = generated_data["persona"].to_list()
            if persona_column:
                # 'required_fields': ['gender', 'name', 'age_group', 'location', 'profession', 'personality', 'communication_style',"accent","language","conversation_speed","finished_speaking_sensitivity","interrupt_sensitivity"]
                required_fields = PersonaConfigurator.get_required_fields(mode)

                # iterate through each row and check if all required fields are present and remove extra fields
                for index, row in enumerate(persona_column):
                    # Convert row to dict if it is string
                    if isinstance(row, str):
                        try:
                            row = json.loads(row)
                            row = {k.lower(): v for k, v in row.items()}
                        except Exception as e:
                            logger.warning(f"Error parsing persona JSON string: {e}")
                            row = {}
                    elif not isinstance(row, dict):
                        row = {}

                    property_dict = PersonaConfigurator.get_property_dict(mode)
                    # Do not override languages
                    # if self.agent_definition.languages:
                    #     property_dict["language"] = self.agent_definition.languages

                    for field in required_fields:
                        # Fill missing required fields with random values
                        if field not in row or not row[field]:
                            logger.debug(
                                f"Row {index + 1} missing required field: {field}"
                            )
                            if field in property_dict:
                                prop_value = property_dict[field]
                                if isinstance(prop_value, list):
                                    row[field] = random.choice(prop_value)
                                else:
                                    row[field] = prop_value
                            else:
                                row[field] = "Not Specified"

                    # Remove extra fields not in required list and preserve required_fields order
                    row = {k: row[k] for k in required_fields if k in row}

                    # CRITICAL: Write back to list (fixes bug where modifications were lost)
                    persona_column[index] = row

                # Update the persona column in generated_data
                generated_data["persona"] = persona_column
            tik_total_tokens = 0
            for col in generated_data.columns:
                for value in generated_data[col]:
                    tik_total_tokens += count_text_tokens(str(value))

            return self._convert_sda_data_to_cases(generated_data)

        except Exception as e:
            logger.exception(f"Error generating raw cases from SDA: {e}")
            return None

    def _enrich_cases_with_branch_data(
        self,
        cases: List[Dict],
        branch_metadata_lookup: Dict[str, Dict],
    ) -> List[Dict]:
        """Enrich cases with branch-specific data and normalize.

        Args:
            cases: Raw cases from SDA with conversation_branch field
            branch_metadata_lookup: Mapping from branch_name to full branch metadata

        Returns normalized cases with correct detailed_path per branch.
        """
        enriched: List[Dict] = []
        unmatched_branches: set = set()

        for case in cases:
            branch_name = case.get("conversation_branch", "")
            branch_metadata = branch_metadata_lookup.get(branch_name)

            if branch_metadata:
                case["detailed_path"] = branch_metadata.get("detailedPath", [])
            else:
                # Log unmatched branches (but only once per branch name)
                if branch_name and branch_name not in unmatched_branches:
                    logger.warning(f"No metadata found for branch: {branch_name}")
                    unmatched_branches.add(branch_name)
                case["detailed_path"] = []

            enriched.append(case)

        if unmatched_branches:
            logger.warning(f"Total unmatched branches: {len(unmatched_branches)}")

        return self._normalize_cases(enriched)

    def _normalize_cases(self, cases: List[Dict]) -> List[Dict]:
        """Normalize cases to allowed node types.

        Allowed node types in output: message, condition, end.
        Start is implicit and removed if present.
        """
        normalized: List[Dict] = []
        # Map of raw type strings to normalized NodeType values
        type_mapping = {
            NodeType.MESSAGE: NodeType.MESSAGE,
            NodeType.CONDITION: NodeType.CONDITION,
            "conditional": NodeType.CONDITION,  # Alias
            NodeType.END: NodeType.END,
        }

        for c in cases:
            name = str(c.get("name", "Case")).strip() or "Case"
            persona_raw = c.get("persona", "")
            persona: Union[Dict, str]
            if isinstance(persona_raw, dict):
                persona = persona_raw
            else:
                persona = str(persona_raw).strip()

            single_situation = str(c.get("situation", "")).strip()
            outcome = str(c.get("outcome", "")).strip()

            nodes: List[Dict] = []
            for n in c.get("nodes", []):
                t_raw = str(n.get("type", "")).lower()

                # Skip start nodes (start is implicit)
                if t_raw == NodeType.START:
                    continue

                # Map to normalized type, skip if not recognized
                normalized_type = type_mapping.get(t_raw)
                if normalized_type is None:
                    continue

                nodes.append(
                    {
                        "type": normalized_type,
                        "label": n.get("label")
                        or (
                            "End"
                            if normalized_type == NodeType.END
                            else NodeType.get_display_name(normalized_type)
                        ),
                        "config": n.get("config") or {},
                        "terminal": bool(
                            n.get("terminal", NodeType.is_terminal(normalized_type))
                        ),
                    }
                )

            conversation_branch = str(c.get("conversation_branch", "")).strip()
            branch_category = str(c.get("branch_category", "")).strip()

            # detailed_path already enriched by _enrich_cases_with_branch_data
            detailed_path = c.get("detailed_path", [])

            normalized_case = {
                "name": name,
                "persona": persona,
                "situation": single_situation,
                "outcome": outcome,
                "conversation_branch": conversation_branch,
                "branch_category": branch_category,
                "nodes": nodes,
                "detailed_path": detailed_path,
            }

            # Add custom columns to normalized case
            if self.custom_columns:
                for column in self.custom_columns:
                    column_name = column.get("name")
                    if column_name and column_name in c:
                        normalized_case[column_name] = c[column_name]

            normalized.append(normalized_case)

        return normalized

    def _convert_sda_data_to_cases(self, generated_data) -> List[Dict]:
        """Convert SDA generated data into scenario cases with nodes for each situation.

        When more rows are generated than requested (to ensure branch diversity),
        this method samples evenly across branches to get the requested number of rows.
        """
        all_cases: List[Dict] = []

        try:
            # Handle None or empty data
            # Extract the generated data from SDA response
            if isinstance(generated_data, dict) and "data" in generated_data:
                data_rows = generated_data["data"]
            elif isinstance(generated_data, list):
                data_rows = generated_data
            else:
                # If it's a DataFrame or other format, convert to list of dicts
                if hasattr(generated_data, "to_dict"):
                    data_rows = generated_data.to_dict("records")
                else:
                    data_rows = generated_data

            # Helper to safely get string value, handling None and NaN
            def safe_str_value(val, default=""):
                if val is None:
                    return default
                if isinstance(val, float) and math.isnan(val):
                    return default
                return str(val) if val else default

            # First, collect ALL cases
            for i, row in enumerate(data_rows):
                if not isinstance(row, dict):
                    logger.warning(f"Skipping non-dict row: {type(row)}")
                    continue

                persona = row.get("persona", "")
                situation = safe_str_value(row.get("situation"), "")
                outcome = safe_str_value(row.get("outcome"), "")
                conversation_branch = safe_str_value(row.get("branch_name"), "")
                branch_category = safe_str_value(row.get("branch_category"), "")

                # Skip if essential data is missing
                if not situation:
                    logger.debug(f"Skipping row {i} - no situation found")
                    continue

                # Handle persona - it might be a dict, JSON string, or Python dict string
                if isinstance(persona, str):
                    try:
                        persona_data = json.loads(persona)
                    except json.JSONDecodeError:
                        try:
                            # Try Python literal evaluation (handles {'key': 'value'} format)
                            persona_data = ast.literal_eval(persona)
                        except (ValueError, SyntaxError):
                            persona_data = {"description": persona}
                elif isinstance(persona, dict):
                    # Clean persona dict to remove None keys
                    persona_data = {k: v for k, v in persona.items() if k is not None}
                else:
                    persona_data = {"description": str(persona) if persona else ""}

                case = {
                    "name": f"Case_{i + 1}_{situation[:20].replace(' ', '_')}",
                    "persona": persona_data,
                    "situation": situation,
                    "outcome": outcome,
                    "conversation_branch": conversation_branch,
                    "branch_category": branch_category,
                }

                # Add custom column values to the case
                if self.custom_columns:
                    for column in self.custom_columns:
                        column_name = column.get("name")
                        if column_name and column_name in row:
                            # Safely handle None/NaN values in custom columns
                            col_val = row[column_name]
                            if col_val is None or (
                                isinstance(col_val, float) and math.isnan(col_val)
                            ):
                                case[column_name] = ""
                            else:
                                case[column_name] = col_val

                all_cases.append(case)

            # If we have more cases than requested, sample evenly across branches
            if self.no_of_rows and len(all_cases) > self.no_of_rows:
                cases = self._sample_cases_across_branches(all_cases, self.no_of_rows)
            else:
                cases = all_cases

        except Exception as e:
            logger.exception(f"Error converting SDA data to cases: {e}")
            cases = all_cases if all_cases else []

        logger.info(
            f"Converted {len(cases)} cases from SDA data (from {len(all_cases)} total)"
        )
        return cases

    def _sample_cases_across_branches(
        self, cases: List[Dict], target_count: int
    ) -> List[Dict]:
        """Sample cases evenly across branches to ensure diversity.

        Args:
            cases: All generated cases
            target_count: Number of cases to return

        Returns:
            Sampled cases with even distribution across branches
        """
        if not cases or target_count <= 0:
            return []

        # Group cases by branch
        branch_to_cases: Dict[str, List[Dict]] = {}
        for case in cases:
            branch = case.get("conversation_branch", "unknown")
            if branch not in branch_to_cases:
                branch_to_cases[branch] = []
            branch_to_cases[branch].append(case)

        branches = list(branch_to_cases.keys())
        num_branches = len(branches)

        if num_branches == 0:
            return cases[:target_count]

        # Calculate how many cases to take from each branch
        # Distribute evenly, with remainder going to first branches
        base_per_branch = target_count // num_branches
        remainder = target_count % num_branches

        sampled_cases: List[Dict] = []
        for i, branch in enumerate(branches):
            branch_cases = branch_to_cases[branch]
            # Take base + 1 for first 'remainder' branches, else just base
            take_count = base_per_branch + (1 if i < remainder else 0)
            # Sample randomly from this branch's cases
            if len(branch_cases) <= take_count:
                sampled_cases.extend(branch_cases)
            else:
                sampled_cases.extend(random.sample(branch_cases, take_count))

        # Shuffle to avoid branch ordering in output
        random.shuffle(sampled_cases)

        logger.info(
            f"Sampled {len(sampled_cases)} cases across {num_branches} branches "
            f"(target: {target_count})"
        )
        return sampled_cases

    def _create_nodes_for_situation(
        self, situation: str, outcome: str
    ) -> Dict[str, Any]:
        """Use LLM to create appropriate nodes for a given situation and its possible outcomes."""
        try:
            sda = SyntheticDataAgent()
            llm = sda.llm

            prompt = SITUATION_NODE_GENERATION_PROMPT.format(
                situation=situation,
                outcome=outcome,
                description=getattr(self.agent_definition, "description", ""),
                language=getattr(self.agent_definition, "language", ""),
            )

            response = llm._get_completion_content(
                messages=[{"role": "user", "content": prompt}]
            )

            try:
                data = json.loads(response)
            except Exception:
                response = response.strip()
                if response.startswith("```json"):
                    response = response[7:]
                if response.endswith("```"):
                    response = response[:-3]
                data = json.loads(response)

            flows = data.get("flow", {})

            # Ensure we return a dict
            if isinstance(flows, dict):
                return flows
            return {}

        except Exception as e:
            logger.exception(f"Error creating nodes with LLM: {e}")
            # Fallback to a simple flow
            return {
                "outcome": "Standard conversation completed",
                "nodes": [
                    {
                        "type": "message",
                        "label": "Assistant Response",
                        "config": {
                            "text": "Thank you for your time. How can I help you today?",
                            "speaker": "assistant",
                        },
                    },
                    {
                        "type": "message",
                        "label": "User Response",
                        "config": {
                            "text": "I need assistance with my inquiry.",
                            "speaker": "user",
                        },
                    },
                    {
                        "type": "end",
                        "label": "End",
                        "config": {"reason": "Standard conversation completed"},
                        "terminal": True,
                    },
                ],
            }

    def _create_conversation_flow_description(self, detailed_path: List[Dict]) -> str:
        """Create a conversation flow description from detailed path"""
        try:
            flow_parts = []

            for i, node_info in enumerate(detailed_path):
                node_name = node_info.get("name", "unknown")
                node_type = node_info.get("type", "conversation")
                prompt = node_info.get("prompt", "")
                first_message = node_info.get("firstMessage", "")
                edge_condition = node_info.get("edgeCondition", {})

                # Add node information
                flow_parts.append(f"Step {i + 1}: {node_name} ({node_type})")

                if prompt:
                    flow_parts.append(f"  Prompt: {prompt}")

                if first_message:
                    flow_parts.append(f"  Message: {first_message}")

                if edge_condition and edge_condition.get("prompt"):
                    flow_parts.append(f"  Condition: {edge_condition['prompt']}")

                flow_parts.append("")  # Empty line for readability

            return "\n".join(flow_parts)

        except Exception as e:
            logger.exception(f"Error creating conversation flow description: {e}")
            return "Error creating conversation flow"

    def _extract_conversation_flow(self, detailed_branch: Dict) -> str:
        """Extract conversation flow from detailed_branch path, keeping only required data"""
        path = detailed_branch.get("path", [])
        detailed_path = detailed_branch.get("detailedPath", [])

        # Extract only essential node information
        flow_nodes = []
        for node in detailed_path:
            node_info = {}
            if node.get("name"):
                node_info = {
                    "name": node["name"],
                }
            if node.get("type"):
                node_info["type"] = node["type"]
            if node.get("messagePlan"):
                node_info["messagePlan"] = node["messagePlan"]
            if node.get("prompt"):
                node_info["prompt"] = node["prompt"]
            if node.get("firstMessage"):
                node_info["firstMessage"] = node["firstMessage"]
            if node.get("edgeCondition"):
                node_info["edgeCondition"] = node["edgeCondition"]
            if node.get("tool"):
                node_info["tool"] = node["tool"]
            if node.get("globalNodePlan"):
                node_info["globalNodePlan"] = node["globalNodePlan"]
            if node.get("variableExtractionPlan"):
                node_info["variableExtractionPlan"] = node["variableExtractionPlan"]

            flow_nodes.append(node_info)

        return json.dumps(flow_nodes, indent=2)

    def _generate_branch_description(
        self, detailed_branch: Dict, use_thread_local_llm: bool = False
    ) -> str:
        """Generate branch description using LLM from node names in the path.

        Args:
            detailed_branch: Branch data with path and node info.
            use_thread_local_llm: If True, creates a new LLM instance for thread-safety.
        """
        path = detailed_branch.get("path", [])
        start_node = detailed_branch.get("start_node") or "unknown"
        end_node = detailed_branch.get("end_node") or "unknown"

        # Create a prompt for the LLM to generate branch description
        # Create a prompt for the LLM to generate branch description
        prompt = BRANCH_DESCRIPTION_PROMPT.format(
            path_nodes=", ".join(path),
            start_node=start_node,
            end_node=end_node,
        )

        try:
            messages = [{"role": "user", "content": prompt}]
            # Use thread-local LLM if requested (for parallel execution)
            llm = self._create_thread_local_llm() if use_thread_local_llm else self.llm
            response = llm._get_completion_content(messages)
            return response.strip().strip('"').strip("'")
        except Exception as e:
            logger.warning(f"Error generating branch description: {e}")
            # Fallback to end node based description
            return f"conversation ending at {end_node.lower().replace('_', ' ')}"

    def _create_branch_metadata_strings(
        self, branch_metadatas: List[Dict]
    ) -> List[Dict]:
        """Create formatted metadata strings for each branch."""
        metadata_payloads: List[Dict] = []
        for metadata in branch_metadatas:
            branch_name = metadata.get("branch_name", "unknown")
            metadata_lines = [
                "Conversation Branch Information:",
                f"- Branch Name: {branch_name}",
                f"- Description: {metadata.get('description', '')}",
                f"- Start Node: {metadata.get('start_node', 'unknown')}",
                f"- End Node: {metadata.get('end_node', 'unknown')}",
                f"- Conversation Flow: {metadata.get('conversation_flow', '')}",
            ]
            metadata_payloads.append(
                {
                    "branch_name": branch_name,
                    "metadata_string": "\n".join(metadata_lines),
                }
            )
        return metadata_payloads

    def _create_branch_metadata_dict(
        self,
        detailed_branch: Dict,
        use_thread_local_llm: bool = False,
    ) -> Dict:
        """
        Build a metadata dictionary for a branch so downstream agents can inject
        branch-specific context into otherwise branch-agnostic payloads.

        Args:
            detailed_branch: Branch data with path and node info.
            use_thread_local_llm: If True, uses thread-local LLM for thread-safety.
        """
        detailed_path = detailed_branch.get("detailedPath", [])
        conversation_flow = self._create_conversation_flow_description(detailed_path)
        branch_description = self._generate_branch_description(
            detailed_branch, use_thread_local_llm=use_thread_local_llm
        )
        # Use path nodes as branch name (more descriptive than end_node)
        path_nodes = detailed_branch.get("path", [])
        branch_name = (
            " -> ".join(path_nodes)
            if path_nodes
            else (detailed_branch.get("end_node") or "unknown")
        )
        start_node = detailed_branch.get("start_node") or "unknown"
        end_node = detailed_branch.get("end_node") or "unknown"

        metadata = {
            "branch_name": branch_name,
            "start_node": start_node,
            "end_node": end_node,
            "path_length": detailed_branch.get("length", 0),
            "path": path_nodes,
            "detailedPath": detailed_path,
            "description": branch_description,
            "conversation_flow": conversation_flow,
        }
        return metadata

    def _create_branch_sda_payload(
        self,
        detailed_branch: Dict,
        user_requirements: Dict,
        rows: int,
        property_list: List[Dict] = None,
        mode: str = "voice",
    ) -> Dict:
        """Create SDA payload for a specific branch using the standard format"""

        # languages = self.agent_definition.languages
        branch_context_footer = "<conv_branch_info>"

        default_properties = PersonaConfigurator.get_property_dict(mode)
        # Do not override language with agent specific languages
        # if languages:
        #     default_properties["language"] = languages

        # Helper to get mode-specific text
        def get_text(voice_text, chat_text):
            return voice_text if mode == "voice" else chat_text

        call_type_label = get_text("Call Type", "Interaction Type")
        call_type_val = "Inbound" if self.agent_definition.inbound else "Outbound"

        interaction_term = get_text("call", "chat session")  # noun
        interacting_term = get_text("calling", "messaging")  # verb

        situation_instruction = get_text(
            "Do not explicitly describe environmental details like traffic noise playing or label emotions directly. Instead, express the customer's situation through natural behavior and context that implies their state (e.g., being in traffic, handling a child at home), without stating sound effects or background cues. Write in third-person.",
            "Do not describe environmental sounds. Focus on the context in which the user is texting (e.g., 'texting while in a meeting', 'messaging from a noisy cafe', 'using voice-to-text while driving'). Include typos or short phrasing if appropriate for the situation. Write in third-person.",
        )

        def build_property_dict(prop_dict: Dict = None) -> Dict:
            """Build property dict from user input or defaults."""
            prop_dict = prop_dict or {}
            result = {}
            # Use defaults from constant, allow user overrides
            for key, default_value in default_properties.items():
                # if key == "language":
                #     # Language uses agent definition languages as default
                #     result[key] = prop_dict.get(key, languages)
                # else:
                result[key] = prop_dict.get(key, default_value)

            # Handle metadata flattening
            if "metadata" in prop_dict:
                for key, value in prop_dict["metadata"].items():
                    result[key] = [value]
            if "additional_instruction" in prop_dict:
                result["additional_instruction"] = prop_dict["additional_instruction"]

            return result

        if property_list:
            property_list_updated = [build_property_dict(p) for p in property_list]
        else:
            property_dict = build_property_dict()
        custom_instruction_str = None
        if self.custom_instruction:
            custom_instruction_str = (
                f"***IMPORTANT USER INSTRUCTION TO FOLLOW***: {self.custom_instruction}"
            )
        # Create SDA payload
        persona_payload = {
            "requirements": {
                "Dataset Name": f"{self.agent_definition.agent_name.lower().replace(' ', '_')}_dataset",
                "Dataset Description": (
                    f"Create realistic customer personas and scenarios for {self.agent_definition.agent_name}. "
                    f"Agent Purpose: {self.agent_definition.description}. "
                    f"Supported Languages: {self.agent_definition.languages}. "
                    f"{call_type_label}: {call_type_val}. "
                    "Focus on scenarios that align with the provided description of the conversation branch "
                    f"{branch_context_footer}"
                ),
                "Objective": (
                    f"Generate training data for {self.agent_definition.agent_name} to handle calls effectively once the "
                    f"conversation follows the branch description provided below. Ensure each record can be adapted to that flow. {custom_instruction_str or ''}"
                    f"{branch_context_footer}"
                ),
                "patterns": (
                    "Focus on the agent purpose, reinforce the outcomes implied by the branch description, and maintain "
                    "realistic variability across personas, situations, and outcomes that can all map to the branch info below."
                    f"{branch_context_footer}"
                ),
            },
            "constraints": [
                {
                    "field": "persona",
                    "type": "json",
                    "content": (
                        "Detailed customer persona profile. For name always generate a realistic full name based on other characteristics. "
                    ),
                    "property": property_dict if not property_list else {},
                },
                {
                    "field": "situation",
                    "type": "text",
                    "content": (
                        f"Specific situation of the customer when they initiate a {interaction_term} with agent: {self.agent_definition.agent_name}. "
                        "Situation should be tightly linked to the customer persona. Include only the current situation of the customer in the context "
                        f"of the agent's purpose of {interacting_term} the customer. Make the situation realistic and contextually relevant to the agent definition, "
                        "and ensure it naturally leads to the provided description of the conversation branch below. "
                        f"{situation_instruction}"
                        f"{branch_context_footer}"
                    ),
                    "property": {
                        "min_length": 30,
                        "max_length": 400,
                        "required_elements": [],
                    },
                },
                {
                    "field": "outcome",
                    "type": "text",
                    "content": (
                        "Create a specific Outcome that reflects how the interaction resolves once the conversation follows the branch description provided below. "
                        f"Base it on the agent purpose of {interacting_term} the customer, considering different customer responses and agent capabilities. "
                        "Outcome should be specific and measurable. Write in third-person past tense, 2-4 sentences (45-90 words), "
                        "describing the customer's final decision, the agent's key actions, concrete details like next steps, and the agent's tone or behavior. "
                        "Avoid dialogue or generic lines. Keep it professional and outcome-focused."
                        f"{branch_context_footer}"
                    ),
                    "property": {
                        "min_length": 30,
                        "max_length": 400,
                        "required_elements": [],
                    },
                },
            ],
            "schema": {
                "persona": {"type": "json"},
                "situation": {"type": "text"},
                "outcome": {"type": "text"},
            },
            "batch_size": rows,  # Use effective batch size for branch diversity
            "generation_type": "simulation" if property_list else "",
            "property_list": property_list_updated if property_list else {},
        }
        logger.info(f">>>>>{persona_payload}")
        # Add custom columns to the payload
        if self.custom_columns:
            for column in self.custom_columns:
                column_name = column.get("name")
                column_type = column.get("data_type", "text")
                column_description = column.get("description", "")

                # Map data types to constraint types
                constraint_type = "text"  # default
                if column_type in ["json", "persona"]:
                    constraint_type = "json"
                elif column_type in ["number", "integer", "float"]:
                    constraint_type = "number"
                elif column_type == "boolean":
                    constraint_type = "boolean"
                elif column_type == "string":
                    constraint_type = "text"
                elif column_type == "datetime":
                    constraint_type = "datetime"
                elif column_type == "array":
                    constraint_type = "array"

                # Add constraint for the custom column
                persona_payload["constraints"].append(
                    {
                        "field": column_name,
                        "type": constraint_type,
                        "content": (
                            f"{column_description}. Generate realistic and contextually relevant data "
                            f"for {self.agent_definition.agent_name} scenarios that can be tailored using the conversation branch information below."
                            f"{branch_context_footer}"
                        ),
                        "property": {
                            "min_length": 10,
                            "max_length": 500,
                            "required_elements": [],
                        }
                        if constraint_type == "text"
                        else {},
                    }
                )

                # Add to schema
                persona_payload["schema"][column_name] = {"type": constraint_type}

        return persona_payload

    def _create_fallback_cases(self, branches: List[Dict]) -> List[Dict]:
        """Create simple fallback cases"""
        cases = []

        for i, branch in enumerate(branches):
            # Use branch directly since get_branch_metadata is not available
            branch_metadata = branch

            case = {
                "name": f"Fallback_Case_{i + 1}",
                "persona": f"Sample customer persona",
                "situation": f"Sample customer situation",
                "outcome": f"Sample outcome",
                "branch_id": branch.get("branch_id"),
                "branch_type": branch.get("branch_type", "standard_flow"),
                "conversation_flow": branch_metadata.get("conversation_flow", []),
                "path": branch.get("path", []),
                "complexity": branch.get("complexity", "moderate"),
            }
            cases.append(case)

        return cases

    def _create_scenario_dataset(
        self, scenario: Scenarios, cases: List[Dict], name: str, description: str
    ) -> Dataset:
        """Create a Dataset with scenario data stored as rows."""
        try:
            with transaction.atomic():
                # Create the dataset
                dataset = Dataset.objects.create(  # type: ignore[misc]
                    id=uuid.uuid4(),
                    name=f"{name} - Scenario Dataset",
                    organization=scenario.organization,
                    workspace=scenario.workspace,
                    source=DatasetSourceChoices.SCENARIO.value,
                    column_order=[],
                    column_config={},
                    dataset_config={
                        "scenario_type": "graph",
                        "agent_definition_id": str(self.agent_definition.id),
                        "agent_name": self.agent_definition.agent_name,
                        "description": description,
                        "total_cases": len(cases),
                    },
                )

                # Define column schema
                column_schema = {
                    "persona": {
                        "data_type": DataTypeChoices.PERSONA.value,
                        "description": "Customer persona profile",
                    },
                    "situation": {
                        "data_type": DataTypeChoices.TEXT.value,
                        "description": "Customer situation or scenario",
                    },
                    "outcome": {
                        "data_type": DataTypeChoices.TEXT.value,
                        "description": "Conversation outcome",
                    },
                    "conversation_branch": {
                        "data_type": DataTypeChoices.TEXT.value,
                        "description": "Branch name in workflow graph",
                    },
                    "branch_category": {
                        "data_type": DataTypeChoices.TEXT.value,
                        "description": "Type of branch in the scenario graph",
                    },
                }

                # Add custom columns to the schema
                if self.custom_columns:
                    for column in self.custom_columns:
                        column_name = column.get("name")
                        column_type = column.get("data_type", "text")
                        column_description = column.get("description", "")

                        # Convert column type to DataTypeChoices value
                        data_type_value = column_type.upper()
                        if hasattr(DataTypeChoices, data_type_value):
                            data_type_value = getattr(
                                DataTypeChoices, data_type_value
                            ).value
                        else:
                            # Default to TEXT if invalid type
                            data_type_value = DataTypeChoices.TEXT.value

                        column_schema[column_name] = {
                            "data_type": data_type_value,
                            "description": column_description,
                        }

                # Create columns and store references (avoid N+1 query later)
                columns_by_name: Dict[str, Column] = {}
                column_uuids: List[str] = []
                column_config: Dict[str, Any] = {}

                for col_name, col_info in column_schema.items():
                    column: Column = Column.objects.create(  # type: ignore[misc]
                        id=uuid.uuid4(),
                        dataset=dataset,
                        name=col_name,
                        data_type=col_info["data_type"],
                        source=SourceChoices.OTHERS.value,
                        metadata={
                            "description": col_info["description"],
                            "simulation_type": (
                                scenario.agent_definition.agent_type
                                if scenario.agent_definition
                                else "text"
                            ),
                        },
                    )
                    columns_by_name[col_name] = column
                    column_uuids.append(str(column.id))
                    column_config[str(column.id)] = {
                        "name": col_name,
                        "type": col_info["data_type"],
                        "description": col_info["description"],
                    }

                    if col_name == "persona":
                        column_config[str(column.id)]["simulation_type"] = (
                            scenario.agent_definition.agent_type
                            if scenario.agent_definition
                            else None
                        )

                # Update dataset with column_order and column_config
                dataset.column_order = column_uuids
                dataset.column_config = column_config
                dataset.save()

                # Create rows and cells for each case
                for i, case in enumerate(cases):
                    row: Row = Row.objects.create(  # type: ignore[misc]
                        id=uuid.uuid4(),
                        dataset=dataset,
                        order=i,
                        metadata={"intent_id": case.get("intent_id")},
                    )

                    conversation_flow = case.get("nodes", [])

                    # Create cells for each column (using cached column references)
                    for col_name, col in columns_by_name.items():
                        # Get the value for this column
                        if col_name == "persona":
                            value = case.get("persona", {})
                        elif col_name == "situation":
                            value = case.get("situation", "")
                        elif col_name == "outcome":
                            value = case.get("outcome", "")
                        elif col_name == "conversation_branch":
                            value = case.get("conversation_branch", "")
                        elif col_name == "branch_category":
                            value = case.get("branch_category", "")
                        elif col_name == "conversation_flow":
                            value = json.dumps(conversation_flow)
                        elif col_name == "scenario_flow":
                            value = json.dumps(case.get("detailed_path", []))
                        elif col_name == "branch_type":
                            value = case.get("branch_type", "")
                        elif col_name == "branch_id":
                            value = case.get("branch_id", "")
                        elif col_name == "complexity":
                            value = case.get("complexity", "")
                        else:
                            # Custom column
                            value = case.get(col_name, "")

                        Cell.objects.create(  # type: ignore[misc]
                            id=uuid.uuid4(),
                            dataset=dataset,
                            column=col,
                            row=row,
                            value=value,
                        )

                logger.info(
                    f"Successfully created dataset {dataset.id} with {len(cases)} cases"
                )
                return dataset

        except Exception as e:
            logger.exception(f"Error creating scenario dataset: {str(e)}")
            raise e
