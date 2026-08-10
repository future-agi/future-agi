import structlog

logger = structlog.get_logger(__name__)
import math
import uuid
from agentic_eval.core.embeddings.embeddings_v2 import get_embedding_model
import json_repair
import traceback
from tfc.telemetry import wrap_for_async
from asgiref.sync import sync_to_async
import asyncio
import re
from typing import Dict, Any, Tuple, Optional, List, Set
from ee.agenthub.synthetic_data_agent.kb_seed_instruction_agent import (
    KBSeedInstructionAgent,
)
from ee.agenthub.scenario_graph.persona_configurator import (
    PersonaConfigurator,
)
from datetime import datetime
from agentic_eval.core.embeddings.embedding_manager import EmbeddingManager

from ee.agenthub.synthetic_data_agent.prompts import (
    PLANNING_PROMPT,
    VALIDATION_PROMPT,
    GENERATION_PROMPT,
    INS_PROMPT,
    PREPARE_PAYLOAD_PROMPT,
    COLUMN_GENERATION_PROMPT,
    BATCH_PROMPT,
    DATA_QUALITY_VALIDATION_PROMPT,
    DATA_REPAIR_PROMPT,
)
from ee.agenthub.synthetic_data_agent.seed_instruction_agent import (
    SeedInstructionAgent,
)
from agentic_eval.core.llm.llm import LLM
import random
from collections import defaultdict

from model_hub.utils.synthetic_task_manager import SyntheticTaskManager
import numpy as np
import pandas as pd

# NOTE: Heavy imports (transformers, scipy) are lazy-loaded where used for faster startup
from agentic_eval.core.utils.model_config import ModelConfigs

# Default model configuration using ModelConfigs
DEFAULT_SDA_MODEL_CONFIG = ModelConfigs.VERTEX_GEMINI_2_5_PRO
import json
import os
import copy

# Constants
MAX_CONCURRENT_LLM_CALLS = 100


class AbortFlow(Exception):
    """Signal early exit through many nested calls."""


# Default max workers for parallel plan processing
DEFAULT_SDA_MAX_WORKERS = 15
# Phase allocation (percentages):
#   - Planning:           0% -  5%  (plan generation)
#   - Data Generation:    5% - 85%  (includes retries + diversity eval)
#   - Post-processing:   85% - 99%  (class distribution, KB coverage, final prep)
#
# Data generation retry allocation (most jobs complete in 1 retry):
#   - Retry 1: 5% - 78%   (main data generation - largest portion)
#   - Retry 2: 78% - 82%  (diversity fix retry if needed)
#   - Retry 3: 82% - 85%  (final diversity fix if needed)
# =================================================================
PROGRESS_PLANNING_START = 0
PROGRESS_PLANNING_MID = 2
PROGRESS_PLANNING_END = 5
PROGRESS_RETRY_1_END = 78
PROGRESS_RETRY_2_END = 82
PROGRESS_DATA_GEN_END = 85
PROGRESS_CLASS_DIST_END = 90
PROGRESS_KB_COVERAGE_END = 95
PROGRESS_FINAL_PREP = 97

# Data generation retry ranges (cumulative)
# Most jobs complete in 1 retry, so give it the largest range
# Subsequent retries get smaller ranges as they're less common
DATA_GEN_RETRY_RANGES = [
    (PROGRESS_PLANNING_END, PROGRESS_RETRY_1_END),  # Retry 1: 5% to 78%
    (PROGRESS_RETRY_1_END, PROGRESS_RETRY_2_END),  # Retry 2: 78% to 82%
    (PROGRESS_RETRY_2_END, PROGRESS_DATA_GEN_END),  # Retry 3: 82% to 85%
]


class SyntheticDataAgent:
    def __init__(
        self,
        model_name=DEFAULT_SDA_MODEL_CONFIG.model_name,
        temperature=DEFAULT_SDA_MODEL_CONFIG.temperature,
        max_tokens=DEFAULT_SDA_MODEL_CONFIG.max_tokens,
        provider=DEFAULT_SDA_MODEL_CONFIG.provider,
        quality_thresholds=None,
        dataset_id=None,
        request_uuid=None,
        simulation_mode="voice",
        max_workers=DEFAULT_SDA_MAX_WORKERS,
    ):
        embedding_manager = EmbeddingManager()

        self.embedding_model = embedding_manager.get_syn_embedding()
        logger.info(
            f"Initializing SyntheticDataAgent with model: {model_name}, temperature: {temperature}, max_tokens: {max_tokens}, provider: {provider}"
        )
        self.llm = LLM(
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            provider=provider,
        )
        self.quality_thresholds = (
            quality_thresholds or self._default_quality_thresholds()
        )
        # Store dataset and request identification for cancellation checks
        self.dataset_id = dataset_id
        self.request_uuid = request_uuid
        self.simulation_mode = simulation_mode
        self.max_workers = max_workers

        # Import task manager for cancellation checks
        try:
            self.task_manager: Optional[SyntheticTaskManager] = SyntheticTaskManager()
        except ImportError:
            self.task_manager = None
            logger.warning(
                "SyntheticTaskManager not available - cancellation checks disabled"
            )

    def update_current_creation(self, percentage):
        """
        Update the current creation progress percentage in the task manager.

        Args:
            percentage (float): The percentage of completion (0.0 to 100.0)
        """

        if not self.task_manager or not self.dataset_id or not self.request_uuid:
            logger.warning(
                "Cannot update progress: missing task manager, dataset_id, or request_uuid"
            )
            return

        try:
            # Ensure percentage is within valid range
            # print("my per is", percentage)
            percentage = max(0.0, min(100.0, float(percentage)))
            # if percentage is 100 update it to 99

            # Update the progress in the task manager
            self.task_manager.update_progress(
                self.dataset_id, self.request_uuid, percentage
            )

            logger.info(
                f"Updated progress for dataset {self.dataset_id} request {self.request_uuid}: {percentage:.1f}%"
            )

        except Exception as e:
            logger.error(
                f"Failed to update progress for dataset {self.dataset_id}: {str(e)}"
            )

    def should_continue(self) -> bool:
        """
        Check if the current synthetic data generation request should continue.
        Returns False if the request has been cancelled or is no longer the latest.

        Returns:
            bool: True if the request should continue, False otherwise
        """
        if not self.task_manager or not self.dataset_id or not self.request_uuid:
            # If we don't have the required info, continue (backward compatibility)
            return True

        try:
            # Check if this request has been cancelled
            if self.task_manager.is_cancelled(self.dataset_id, self.request_uuid):
                logger.info(
                    f"Request {self.request_uuid} for dataset {self.dataset_id} has been cancelled"
                )
                return False

            # Check if this is still the latest request
            if not self.task_manager.is_latest_request(
                self.dataset_id, self.request_uuid
            ):
                logger.info(
                    f"Request {self.request_uuid} for dataset {self.dataset_id} is no longer the latest"
                )
                return False

            return True

        except Exception as e:
            logger.error(f"Error checking if request should continue: {str(e)}")
            # On error, continue to avoid blocking the process
            return True

    async def should_continue_async(self) -> bool:
        """
        Async version of should_continue for use in async contexts.
        Wraps Django ORM/cache operations with sync_to_async.
        """
        if not self.task_manager or not self.dataset_id or not self.request_uuid:
            return True

        try:
            # Wrap sync cache operations with sync_to_async
            is_cancelled = await sync_to_async(
                self.task_manager.is_cancelled, thread_sensitive=False
            )(self.dataset_id, self.request_uuid)
            if is_cancelled:
                logger.info(
                    f"Request {self.request_uuid} for dataset {self.dataset_id} has been cancelled"
                )
                return False

            is_latest = await sync_to_async(
                self.task_manager.is_latest_request, thread_sensitive=False
            )(self.dataset_id, self.request_uuid)
            if not is_latest:
                logger.info(
                    f"Request {self.request_uuid} for dataset {self.dataset_id} is no longer the latest"
                )
                return False

            return True

        except Exception as e:
            logger.error(f"Error checking if request should continue: {str(e)}")
            return True

    async def update_current_creation_async(self, percentage):
        """
        Async version of update_current_creation for use in async contexts.
        Wraps Django ORM/cache operations with sync_to_async.
        """
        if not self.task_manager or not self.dataset_id or not self.request_uuid:
            logger.warning(
                "Cannot update progress: missing task manager, dataset_id, or request_uuid"
            )
            return

        try:
            percentage = max(0.0, min(100.0, float(percentage)))
            await sync_to_async(
                self.task_manager.update_progress, thread_sensitive=False
            )(self.dataset_id, self.request_uuid, percentage)
            logger.info(
                f"Updated progress for dataset {self.dataset_id} request {self.request_uuid}: {percentage:.1f}%"
            )
        except Exception as e:
            logger.error(
                f"Failed to update progress for dataset {self.dataset_id}: {str(e)}"
            )

    @staticmethod
    def _build_branch_context(branch_metadata: str | None) -> str:
        """Build the branch context string for prompt injection.

        Args:
            branch_metadata: The branch metadata string or None.

        Returns:
            Formatted branch context string for the prompt.
        """
        if not branch_metadata:
            return ""
        return (
            "\n**CONVERSATION BRANCH CONTEXT:**\n"
            "The generated values should align with this specific conversation branch:\n"
            f"{branch_metadata}\n"
        )

    async def generate_column_data(self, payload, branch_metadatas=None) -> Any:
        """
        Generate data for a specific column based on the provided payload. Processes rows in parallel instead of batches to reduce context bleeding and improve performance by multitudes.

        Args:
            payload (dict): The payload containing requirements, constraints, and reference data.

        Returns:
            pd.DataFrame: DataFrame with generated column data.

        Raises:
            ValueError: If required fields are missing or data generation fails.
        """
        # Validate required fields
        required_fields = ["requirements", "constraints", "reference_data", "schema"]
        for field in required_fields:
            if not payload.get(field):
                raise ValueError(f"{field} is required")

        # Limit concurrent LLM calls to prevent overwhelming the API
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_LLM_CALLS)

        async def generate_data_for_row(
            row, requirements, constraint, schema, llm, branch_context=""
        ):
            async with semaphore:
                try:
                    column_generation_prompt = COLUMN_GENERATION_PROMPT.format(
                        constraints=json.dumps(
                            constraint, indent=2, ensure_ascii=False
                        ),
                        row_data=json.dumps(
                            row.to_dict(), indent=2, ensure_ascii=False
                        ),
                        requirements=json.dumps(
                            requirements, indent=2, ensure_ascii=False
                        ),
                        schema=json.dumps(schema, indent=2, ensure_ascii=False),
                        branch_context=branch_context,
                    )
                    if not await self.should_continue_async():
                        # optionally attach context/payload
                        raise AbortFlow("Stopped because check failed")
                    response = await llm._get_completion_content_async(
                        messages=[
                            {"role": "user", "content": column_generation_prompt}
                        ],
                        response_format={"type": "json_object"},
                    )
                    try:
                        generated_value = json.loads(response)
                        return generated_value
                    except (json.JSONDecodeError, KeyError) as e:
                        # logger.error(
                        #     f"Error parsing response for : {str(e)}, trying repair with json_repair..."
                        # )
                        generated_value = json_repair.loads(response)
                        return generated_value

                except Exception as e:
                    logger.exception(f"Error generating data for row: {str(e)}")
                    return None

        try:
            # Create DataFrame from reference data
            df = pd.DataFrame(payload["reference_data"])

            # Create tasks for all rows
            tasks = []
            for idx, (_, row) in enumerate(df.iterrows()):
                branch_metadata = (
                    branch_metadatas[idx]
                    if branch_metadatas and idx < len(branch_metadatas)
                    else None
                )
                branch_context = self._build_branch_context(branch_metadata)
                tasks.append(
                    generate_data_for_row(
                        row,
                        payload["requirements"],
                        payload["constraints"],
                        payload["schema"],
                        self.llm,
                        branch_context,
                    )
                )

            # Execute all tasks in parallel
            all_generated_values = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results and handle exceptions
            processed_values = []
            for i, result in enumerate(all_generated_values):
                if isinstance(result, Exception):
                    logger.error(
                        f"Row at position {i} generation failed with exception: {result}"
                    )
                    processed_values.append(None)
                else:
                    processed_values.append(result)

            new_df = pd.DataFrame(processed_values)

            # Check for failed generations (None values)

            failed_row_indices = new_df[new_df.isna().any(axis=1)].index.tolist()
            retry_count = 0
            max_retries = 2
            while failed_row_indices and retry_count < max_retries:
                logger.warning(
                    f"Warning: Failed to generate for {len(failed_row_indices)} rows. Retry attempt {retry_count + 1}/{max_retries}..."
                )

                # Retry failed rows individually
                retry_tasks = []
                for i_retry, idx in enumerate(failed_row_indices):
                    row_to_retry = df.iloc[idx]
                    branch_metadata = (
                        branch_metadatas[idx]
                        if branch_metadatas and idx < len(branch_metadatas)
                        else None
                    )
                    branch_context = self._build_branch_context(branch_metadata)
                    retry_tasks.append(
                        generate_data_for_row(
                            row_to_retry,
                            payload["requirements"],
                            payload["constraints"],
                            payload["schema"],
                            self.llm,
                            branch_context,
                        )
                    )

                # Execute retry tasks in parallel
                retry_results = await asyncio.gather(
                    *retry_tasks, return_exceptions=True
                )

                # Update the dataframe with retry results
                for i, idx in enumerate(failed_row_indices):
                    retry_value = retry_results[i]
                    if (
                        not isinstance(retry_value, Exception)
                        and retry_value is not None
                    ):
                        for key, value in retry_value.items():
                            new_df.loc[idx, key] = value

                # Increment retry count
                retry_count += 1

                # Recalculate failed row indices for next iteration
                failed_row_indices = new_df[new_df.isna().any(axis=1)].index.tolist()

            return new_df

        except Exception as e:
            raise ValueError(f"Failed to generate column data: {str(e)}")

    def generate_and_validate(
        self,
        payload,
        branch_metadatas=None,
        dataset_id=None,
        headers=None,
        base_url=None,
        called_for=None,
    ):
        """
        Synchronous wrapper for backward compatibility.
        Uses asyncio.run() to execute async implementation.
        """
        return asyncio.run(
            self._generate_and_validate_async(
                payload, branch_metadatas, dataset_id, headers, base_url, called_for
            )
        )

    async def _generate_and_validate_async(
        self,
        payload,
        branch_metadatas=None,
        dataset_id=None,
        headers=None,
        base_url=None,
        called_for=None,
    ):
        """Generates synthetic data using multiple plans and returns a structured response with validation"""
        simulation_flag = False
        try:
            await self.update_current_creation_async(PROGRESS_PLANNING_START)

            prompt_type = (
                "generation_type" in payload and payload["generation_type"] == "prompt"
            )
            if prompt_type:
                _cfg = ModelConfigs.VERTEX_GEMINI_2_5_FLASH_LITE
                self.llm = LLM(
                    model_name=_cfg.model_name,
                    temperature=_cfg.temperature,
                    max_tokens=_cfg.max_tokens,
                    provider=_cfg.provider,
                )
                payload, space_map = await self.prepare_payload_async(payload)
                payload["generation_type"] = "prompt"
            elif (
                "generation_type" in payload
                and payload["generation_type"] == "simulation"
            ) or branch_metadatas:
                simulation_flag = True

            org_payload = payload.copy()
            seed_agent = None

            # Input validation
            required_fields = ["requirements", "schema", "constraints"]
            for field in required_fields:
                if not payload.get(field) and not "reference_data" in payload:
                    raise ValueError(f"{field} is required")
            chunk_ids: Set[Any] = set()
            if payload["batch_size"] <= 0:
                return {"data": [], "rows": []}
            num_plans = min(30, math.ceil(payload["batch_size"] / 10))
            datapoints_per_plan = math.ceil(payload["batch_size"] / num_plans)
            number_of_iterations_per_plan = math.ceil(datapoints_per_plan / 5)

            # Update progress: Planning in progress
            await self.update_current_creation_async(PROGRESS_PLANNING_MID)

            plans, params, seed_agent, total_chunks = await asyncio.to_thread(
                self._generate_plans, payload, num_plans
            )
            logger.info(f"Generated {len(plans)} plans.")

            # Update progress: Planning complete
            await self.update_current_creation_async(PROGRESS_PLANNING_END)

            if "error" in plans:
                return {
                    "message": "Failed to generate plans. Please check the input parameters and try again.",
                }
            all_generated_data: Dict[str, Any] = {"plans": {}}
            reserve_pool: List[
                Dict[str, Any]
            ] = []  # Store discarded datapoints for potential reuse
            rows = None
            if dataset_id:
                row_ids = [str(uuid.uuid4()) for _ in range(payload["batch_size"])]
                rows = [{"id": row_id, "cells": []} for row_id in row_ids]

            # Generate data for each plan
            # Prompt type generates ≤10 rows — skip diversity retries
            max_retries = 1 if prompt_type else 3
            retry_count = 0
            plans_to_discard = [
                "dummy"
            ]  # Initialize with a non-empty value to ensure the first iteration

            while plans_to_discard and retry_count < max_retries:
                mu, sigma = 0.5, 0.15  # Mean and standard deviation
                temperature = random.gauss(mu, sigma)
                temperature = max(0, min(1, temperature))
                self.llm.temperature = temperature

                if retry_count > 0:
                    logger.info("Fixing semantic diversity...")

                retry_count += 1
                idx = 0
                base_datapoints_per_plan = payload["batch_size"] // num_plans
                remainder = payload["batch_size"] % num_plans
                dp_per_plan_list = [
                    base_datapoints_per_plan + 1
                    if i < remainder
                    else base_datapoints_per_plan
                    for i in range(num_plans)
                ]
                # print("dp2 ",datapoints_per_plan)
                # number_of_iterations_per_plan = math.ceil(datapoints_per_plan / 5)
                plan_infos = [
                    (i, plan_id, plan_details)
                    for i, (plan_id, plan_details) in enumerate(plans.items())
                    if i < num_plans
                ]

                planid_branch_name_lookup: Dict[str, Optional[str]] = {}
                payload_branch_names: List[Optional[str]] = []

                if simulation_flag:
                    payload_list = []
                    property_list = payload.get("property_list", [])
                    # check if property_list is empty dict
                    if not property_list and not branch_metadatas:
                        simulation_flag = False
                    else:
                        num_persona = len(property_list) if property_list else 1
                        len_plans = len(dp_per_plan_list)

                        if branch_metadatas:
                            num_branches = len(branch_metadatas)

                        for i in range(len_plans):
                            p = copy.deepcopy(payload)

                            if property_list:
                                persona_src = property_list[i % num_persona]
                                persona = dict(persona_src)
                                # for property_dict in property_list:
                                if "additional_instruction" in persona:
                                    if "description" not in p["constraints"][0]:
                                        p["constraints"][0]["description"] = ""
                                    p["constraints"][0]["description"] += (
                                        f"Always generate single values for the given property. Do not group properties or generate nested dictionaries. **Addtional instruction provided by user: {persona['additional_instruction']}**"
                                    )
                                    del persona["additional_instruction"]
                                p["constraints"][0]["property"] = persona

                            if branch_metadatas:
                                metadata_entry = branch_metadatas[i % num_branches]
                                metadata_string = metadata_entry.get("metadata_string")
                                branch_name = metadata_entry.get("branch_name")
                                payload_branch_names.append(branch_name)
                                if not metadata_string:
                                    continue
                                marker = "<conv_branch_info>"

                                def replace_marker(value: Any) -> Any:
                                    if isinstance(value, str) and marker in value:
                                        return value.replace(marker, metadata_string, 1)
                                    return value

                                requirements = p.get("requirements")
                                if isinstance(requirements, dict):
                                    for key, value in requirements.items():
                                        requirements[key] = replace_marker(value)

                                constraints = p.get("constraints")
                                if isinstance(constraints, list):
                                    for constraint in constraints:
                                        if (
                                            isinstance(constraint, dict)
                                            and "content" in constraint
                                        ):
                                            constraint["content"] = replace_marker(
                                                constraint["content"]
                                            )
                                payload_list.append(copy.deepcopy(p))

                # Wrap async function with OTel context propagation
                wrapped_generate_plan_data = wrap_for_async(
                    self._generate_plan_data_async
                )

                tasks: List[asyncio.Task] = []
                task_metadata: Dict[asyncio.Task, Dict[str, Any]] = {}
                if simulation_flag:
                    for idx, (plan_info, payload_obj) in enumerate(
                        zip(plan_infos, payload_list)
                    ):
                        branch_name = None
                        if idx < len(payload_branch_names):
                            branch_name = payload_branch_names[idx]
                        task = asyncio.create_task(
                            wrapped_generate_plan_data(
                                plan_info,
                                retry_count,
                                payload_obj,
                                seed_agent,
                                all_generated_data,
                                dataset_id,
                                headers,
                                base_url,
                                params,
                                dp_per_plan_list,
                                rows,
                                called_for,
                            )
                        )
                        task_metadata[task] = {
                            "plan_info": plan_info[1],
                            "payload": payload_obj,
                            "branch_name": branch_name,
                        }
                        tasks.append(task)
                else:
                    for plan_info in plan_infos:
                        task = asyncio.create_task(
                            wrapped_generate_plan_data(
                                plan_info,
                                retry_count,
                                payload,
                                seed_agent,
                                all_generated_data,
                                dataset_id,
                                headers,
                                base_url,
                                params,
                                dp_per_plan_list,
                                rows,
                                called_for,
                            )
                        )
                        task_metadata[task] = {
                            "plan_info": plan_info[1],
                            "payload": None,
                        }
                        tasks.append(task)

                # NOTE: We intentionally avoid `asyncio.as_completed(tasks)` here because it yields
                # wrapper coroutines (not the original `Task` objects). We need the original task
                # identity to look up `task_metadata[task]` (branch_name/payload), otherwise branch
                # mapping (plan_branch_names → branch_name/branch_category) ends up empty.
                completed_count = 0
                pending_tasks = set(tasks)

                # Get progress range for current retry (retry_count is 1-indexed here)
                retry_idx = min(retry_count - 1, len(DATA_GEN_RETRY_RANGES) - 1)
                retry_start, retry_end = DATA_GEN_RETRY_RANGES[retry_idx]

                while pending_tasks:
                    done_tasks, pending_tasks = await asyncio.wait(
                        pending_tasks, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in done_tasks:
                        completed_count += 1
                        # Calculate progress within current retry's allocated range
                        progress_in_retry = (completed_count / len(tasks)) * (
                            retry_end - retry_start
                        )
                        completed_percentage = int(retry_start + progress_in_retry)
                        # Use async version for Django ORM/cache operations
                        await self.update_current_creation_async(completed_percentage)
                        plan_id = task_metadata.get(task, {}).get("plan_info")
                        sim_payload = task_metadata.get(task, {}).get("payload")

                        # get branch name for the specific future
                        plan_branch_name = None
                        if simulation_flag and branch_metadatas:
                            plan_branch_name = task_metadata.get(task, {}).get(
                                "branch_name"
                            )

                        try:
                            result_plan_id, plan_data, plan_chunk_ids = task.result()
                            plan_key = f"itr_{retry_count}_{result_plan_id}"
                            if simulation_flag and sim_payload:
                                # only validate if we passed the persona
                                property_list = payload.get("property_list", [])
                                if property_list:
                                    plan_data = (
                                        self._validate_simulation_generated_data(
                                            plan_data, sim_payload
                                        )
                                    )
                                # create a lookup table for plan_key -> branch_name
                                if plan_branch_name is not None:
                                    planid_branch_name_lookup[plan_key] = (
                                        plan_branch_name
                                    )
                            all_generated_data["plans"][plan_key] = plan_data
                            chunk_ids.update(plan_chunk_ids)
                        except Exception as e:
                            logger.exception(
                                f"Plan {plan_id} generated an exception: {e}"
                            )

                logger.info(
                    f"Finished data generation for all plans. {len(all_generated_data['plans'])} plans have generated data."
                )
                # if len(plans_to_discard) != 0:
                #     if plans_to_discard[0] == "dummy":
                #         file_path = self._save_generated_data(
                #             all_generated_data,
                #             "_pre_semantic_diversity.json",
                #             params["requirements"]["Dataset Name"],
                #         )

                # Evaluate diversity and update plans_to_discard
                if prompt_type:
                    # Skip diversity evaluation for prompt type (≤10 rows)
                    plans_to_discard = []
                elif not simulation_flag:
                    logger.info("Starting diversity evaluation...")
                    plans_to_discard = (
                        self.diversity_evaluation(all_generated_data)
                        if retry_count != max_retries
                        else []
                    )
                    logger.info(
                        f"Plans to discard after diversity evaluation: {plans_to_discard}"
                    )
                if simulation_flag:
                    plans_to_discard = []
                    retry_count = max_retries
                # end_time = datetime.now()
                # elapsed_time = (end_time - start_time).total_seconds()
                # print(f"new create query took {elapsed_time:.2f} seconds to execute")

                if retry_count > 0:
                    temp_generated_data = all_generated_data.copy()

                # add our lookup table
                if planid_branch_name_lookup:
                    all_generated_data["plan_branch_names"] = (
                        planid_branch_name_lookup.copy()
                    )

                new_plans = []
                for key, plan_data in all_generated_data["plans"].items():
                    plan_details = plan_data.get("plan_details")
                    if not isinstance(plan_details, dict):
                        continue
                    name = plan_details.get("name")
                    if name and name in plans_to_discard:
                        new_plans.append(plan_details)

                # Capture discarded plan datapoints into reserve pool before removing
                for key, value in all_generated_data["plans"].items():
                    plan_details = value.get("plan_details", {})
                    if isinstance(plan_details, dict):
                        plan_name = plan_details.get("name")
                        if plan_name and plan_name in plans_to_discard:
                            # Extract datapoints from this discarded plan
                            generated_data = value.get("generated_data", {})
                            for datapoint in generated_data.values():
                                if isinstance(datapoint, dict):
                                    reserve_pool.append(datapoint)

                if reserve_pool:
                    logger.info(
                        f"Added {len(reserve_pool)} datapoints to reserve pool from discarded plans"
                    )

                # Remove discarded plans from all_generated_data and prepare new plans
                all_generated_data["plans"] = {
                    key: value
                    for key, value in all_generated_data["plans"].items()
                    if isinstance(value.get("plan_details", {}), dict)
                    and value["plan_details"].get("name")
                    and value["plan_details"]["name"] not in plans_to_discard
                }

                plans = {}
                plans = {i + 1: plan for i, plan in enumerate(new_plans)}

            # if not plans_to_discard:
            #     logger.info("Processing completed successfully.")
            # elif retry_count >= max_retries:
            #     logger.info("Max retries reached. Exiting loop.")

            # Update progress: Data generation and diversity evaluation complete
            await self.update_current_creation_async(PROGRESS_DATA_GEN_END)

            # Consolidate the final data
            final_generated_data: Dict[str, Any] = {"plans": {}}

            for key, plan_data in all_generated_data["plans"].items():
                # Keep only the latest iteration data
                final_generated_data["plans"][key] = plan_data
            if not final_generated_data["plans"]:
                final_generated_data = temp_generated_data.copy()

            # add the lookup table to final generated data, still as a key named plan_branch_names,
            # column name for the branch_name decided in prepare_final_dataframe
            if "plan_branch_names" in all_generated_data:
                filtered_branch_names = {
                    key: value
                    for key, value in all_generated_data["plan_branch_names"].items()
                    if key in final_generated_data["plans"]
                }
                if filtered_branch_names:
                    final_generated_data["plan_branch_names"] = filtered_branch_names

            # file_path = self._save_generated_data(
            #     final_generated_data,
            #     "_pre_class_distribution.json",
            #     params["requirements"]["Dataset Name"],
            # )

            # print total number of datapoints at this point
            total_datapoints = sum(
                len(plan_data.get("generated_data", {}))
                for plan_data in final_generated_data["plans"].values()
            )
            print(
                f"Total datapoints before class distribution adjustment: {total_datapoints}"
            )

            ##check class distribution here
            # logger.info(
            #     "Checking class distribution and performing iterative correction if needed..."
            # )
            holistic_results = final_generated_data.copy()
            for constraint in params["constraints"]:
                if "distribution" in constraint:
                    distribution = constraint["distribution"]
                    classes = constraint["values"]
                    assert len(distribution) == len(classes), (
                        f"The lengths of 'distribution' and 'classes' must be equal in {constraint[field]}."
                    )
                    expected_distribution = {
                        self.normalize_text(cls): dist
                        for cls, dist in zip(classes, distribution)
                    }

                    holistic_results = self.iterative_correction_holistic(
                        params,
                        final_generated_data,
                        category_column=constraint["field"],
                        expected_distribution=expected_distribution,
                    )
                    if "plans" not in holistic_results or not holistic_results["plans"]:
                        holistic_results = final_generated_data.copy()

            # Update progress: Class distribution complete
            await self.update_current_creation_async(PROGRESS_CLASS_DIST_END)

            # print total number of datapoints at this point
            total_datapoints = sum(
                len(plan_data.get("generated_data", {}))
                for plan_data in holistic_results["plans"].values()
            )
            print(
                f"Total datapoints after class distribution adjustment: {total_datapoints}"
            )
            # ----- KB Chunk Coverage (Balancing) Branch -----
            if payload and "knowledge_base" in payload:
                holistic_results_before_coverage = holistic_results.copy()
                # file_path = self._save_generated_data(
                #     holistic_results_before_coverage,
                #     "_before_coverage.json",
                #     params["requirements"]["Dataset Name"],
                # )

                fraction_of_distict_slots = len(chunk_ids) / (payload["batch_size"] * 4)

                corpus_coverage = len(chunk_ids) / (total_chunks)
                final_metric = 0.5 * fraction_of_distict_slots + 0.5 * corpus_coverage
                if final_metric < 0.7:
                    logger.warning(
                        "Warning: Coverage is less than 70%. Please increase the number of datapoints."
                    )
                    # Use the retrieved chunk_ids from the generation phase.
                    holistic_results = self._increase_coverage(
                        holistic_results_before_coverage,
                        payload,
                        retrieved_chunk_ids=chunk_ids,
                    )

                # Update progress: KB coverage complete
                await self.update_current_creation_async(PROGRESS_KB_COVERAGE_END)

            # file_path = self._save_generated_data(
            #     holistic_results,
            #     "final_generated_data.json",
            #     params["requirements"]["Dataset Name"],
            # )

            # Update progress: Preparing final dataframe
            await self.update_current_creation_async(PROGRESS_FINAL_PREP)

            column_names = params["schema"].keys()
            logger.info(f"Preparing final dataframe with columns: {list(column_names)}")
            final_df = self.prepare_final_dataframe(holistic_results, column_names)
            target_rows = org_payload["batch_size"]

            # Handle row count mismatch
            if len(final_df) < target_rows:
                shortage = target_rows - len(final_df)
                logger.warning(
                    f"Final DataFrame has {len(final_df)} rows, need {target_rows}. Shortage: {shortage}"
                )

                # Try to fill from reserve pool first
                if reserve_pool:
                    rows_to_add = min(shortage, len(reserve_pool))
                    # Randomly select from reserve pool
                    selected_reserves = (
                        random.sample(reserve_pool, rows_to_add)
                        if rows_to_add <= len(reserve_pool)
                        else reserve_pool
                    )

                    # Filter reserve datapoints to only include columns in the final DataFrame
                    column_names_list = list(column_names)
                    filtered_reserves = [
                        {col: dp.get(col, None) for col in column_names_list}
                        for dp in selected_reserves
                    ]

                    reserve_df = pd.DataFrame(filtered_reserves)
                    final_df = pd.concat([final_df, reserve_df], ignore_index=True)
                    logger.info(
                        f"Added {len(filtered_reserves)} rows from reserve pool. New total: {len(final_df)}"
                    )

                # If still not enough, add placeholder rows
                if len(final_df) < target_rows:
                    remaining_shortage = target_rows - len(final_df)
                    logger.warning(
                        f"Still short by {remaining_shortage} rows after using reserve pool. Adding placeholder rows."
                    )
                    # Create placeholder rows
                    column_names_list = list(column_names)
                    placeholder_rows = [
                        {
                            col: "Could not generate this datapoint"
                            for col in column_names_list
                        }
                        for _ in range(remaining_shortage)
                    ]
                    placeholder_df = pd.DataFrame(placeholder_rows)
                    final_df = pd.concat([final_df, placeholder_df], ignore_index=True)

                # Also fill any null values in existing rows
                for col in final_df.columns:
                    final_df[col] = final_df[col].apply(
                        lambda x: (
                            x if pd.notnull(x) else "Could not generate this datapoint"
                        )
                    )

            # Truncate if we have more rows than needed
            elif len(final_df) > target_rows:
                logger.info(
                    f"Final DataFrame has {len(final_df)} rows, truncating to {target_rows}"
                )
                final_df = final_df.iloc[:target_rows]

            # if dataset_id:
            #     self._update_rows_in_dataset(dataset_id, rows, headers, base_url)
            if "generation_type" in payload and payload["generation_type"] == "prompt":
                valid_mappings = {
                    col: space_map[col] for col in final_df.columns if col in space_map
                }
                final_df.rename(columns=valid_mappings, inplace=True)
            logger.info(f"Successfully generated final DataFrame: {final_df}")
            return final_df
        except AbortFlow as e:
            print("abroted")
            logger.exception(f"Aborted flow: {str(e)}")

    def _generate_plans(self, payload, num_plans) -> Any:
        plans: Dict[Any, Any] = {}
        params = payload
        seed_agent = None
        total_chunks = None

        if "reference_data" in payload:
            df = pd.DataFrame(payload["reference_data"])
            seed_agent = SeedInstructionAgent()
            payload = asyncio.run(seed_agent.infer_payload(payload, df))
            df, category_plans = asyncio.run(
                seed_agent.generate_plans_from_examples(payload, df, num_plans)
            )
            i = 1
            num_fewshots = 4
            # Ensure category_plans is a dictionary
            if isinstance(category_plans, dict):
                for category, category_plan in category_plans.items():
                    data_points = category_plan["datapoints"][:num_fewshots]
                    for characteristic, plan in category_plan.items():
                        if characteristic == "datapoints":
                            continue
                        plans[i] = {
                            "Skill": category,
                            "Characteristic": characteristic,
                            "name": f"{category}_{characteristic}",
                            "Plan": plan,
                            "Datapoints": data_points,
                        }
                        if i == num_plans:
                            break
                        i += 1
        elif "knowledge_base" in payload:
            params["num_plans"] = num_plans
            kb_config = payload.get("knowledge_base", {})
            table_name = (
                kb_config.get("table_name", "") if isinstance(kb_config, dict) else ""
            )
            doc_ids = (
                kb_config.get("doc_ids", []) if isinstance(kb_config, dict) else []
            )
            kb_seed_agent = KBSeedInstructionAgent(
                table_name=table_name, doc_ids=doc_ids
            )
            result_dict = kb_seed_agent.generate_plans_from_kb(payload, num_plans)
            seed_data_points = result_dict.get("seed_data_points", [])
            kb_category_plans_raw = result_dict.get("category_plans", {})
            # Ensure kb_category_plans is a dict
            kb_category_plans: Dict[str, Any] = (
                kb_category_plans_raw if isinstance(kb_category_plans_raw, dict) else {}
            )
            total_chunks = result_dict.get("total_chunks")
            seed_agent = kb_seed_agent  # type: ignore[assignment]
            i = 1
            for category, category_plan in kb_category_plans.items():
                data_points = category_plan["datapoints"][:4]
                for characteristic, plan in category_plan.items():
                    if characteristic == "datapoints":
                        continue
                    datapoints_text = []
                    for dp in data_points:
                        datapoints_text.append(dp.get("chunk_text", ""))
                    plans[i] = {
                        "Skill": category,
                        "Characteristic": characteristic,
                        "name": f"{category}_{characteristic}",
                        "Plan": plan,
                        "GROUNDING INSTRUCTION": "STRICTLY use the provided datapoints from the knowledge base. Do NOT introduce information or assumptions outside the provided data points. If any required information is not explicitly in the provided datapoints, do NOT attempt to generate it. There should be **ZERO** hallucinations. (You are allowed to do domain expansion for generating a datapoint. For example: if we know from supplied domain knowledge that the context is about a restaurant, we will generate restaurant names in name column unless clearly specified).",
                        "Datapoints": datapoints_text,
                    }
                    if i == num_plans:
                        break
                    i += 1
        else:
            # if "generation_type" in payload:
            #     payload = self.prepare_payload(payload)
            # Get formatted prompt and parameters
            content, params = self._get_prompt(payload)
            content_list = content.copy()

            # Planning Phase - Get 10 different plans
            params["num_plans"] = num_plans
            content.append({"type": "text", "text": PLANNING_PROMPT.format(**params)})

            retries = 0
            while retries < 3:
                if not self.should_continue():
                    # optionally attach context/payload
                    raise AbortFlow("Stopped because check failed")

                planning_response = self.llm._get_completion_content(
                    messages=[{"role": "user", "content": content}],
                    response_format={"type": "json_object"},
                )
                try:
                    # remove ```json from beginning and ``` from end if it exists
                    if planning_response.startswith("```json"):
                        planning_response = planning_response[7:]
                    if planning_response.endswith("```"):
                        planning_response = planning_response[:-3]
                    # planning_response = planning_response.strip()
                    plans_response: Any = json.loads(planning_response)
                except json.JSONDecodeError:
                    # logger.error(
                    #     "Error: Failed to parse planning response into JSON. Attempting to fix ..."
                    # )
                    plans_response = json_repair.loads(planning_response)

                if isinstance(plans_response, list):
                    plans = {str(i + 1): plan for i, plan in enumerate(plans_response)}
                elif isinstance(plans_response, dict):
                    plans = plans_response
                else:
                    plans = {}

                # Check if plans is valid
                if (
                    isinstance(plans, dict)
                    and len(plans) >= num_plans
                    and all(isinstance(k, str) for k in plans.keys())
                ):
                    if len(plans) > num_plans:
                        plans = {k: plans[k] for k in list(plans.keys())[:num_plans]}
                    break  # Success

                retries += 1
                logger.warning(
                    f"Attempt {retries}: Generated {len(plans) if isinstance(plans, dict) else 0}/{num_plans} plans. Retrying."
                )

        return plans, params, seed_agent, total_chunks

    def _generate_plan_data(
        self,
        plan_info: Tuple[int, str, Dict[str, Any]],
        retry_count: int,
        payload: Dict,
        seed_agent,
        all_generated_data,
        dataset_id,
        headers,
        base_url,
        params: Dict,
        dp_per_plan_list: list,
        rows=None,
        called_for=None,
    ) -> Tuple[str, Dict, Set[Any]]:
        chunk_ids: Set[Any] = set()
        i, plan_id, plan_details = plan_info
        datapoints_per_plan = dp_per_plan_list[i]
        number_of_iterations_per_plan = math.ceil(datapoints_per_plan / 5)
        ins = self.get_ins(payload, plan_details, datapoints_per_plan)
        if retry_count > 0:
            plan_details["Standing Instruction"] = (
                "Previously generated data points from this plan was discarded because semantic diversity was very low. Generate diverse datapoints this time."
            )

        # Generate 10 datapoints for current plan
        plan_data: List[Any] = []
        batch_try = 1
        few_shots_per_datapoint = {}
        if "knowledge_base" in payload:
            # print("ins",ins)
            few_shots_per_datapoint = seed_agent.fetch_few_shots_per_datapoint(
                ins, plan_details, 4
            )
            # print("very outside",few_shots_per_datapoint)
            for i, few_shots in few_shots_per_datapoint.items():
                for few_shot in few_shots:
                    chunk_ids.add(few_shot["chunk_id"])
        for itr in range(number_of_iterations_per_plan):
            max_batch_size = 5
            if "knowledge_base" in payload:
                max_batch_size = 1
            fix_batch_size = 5
            if (
                itr == number_of_iterations_per_plan - 1
                and datapoints_per_plan % fix_batch_size != 0
            ):
                remaining_data_points = datapoints_per_plan % fix_batch_size
                fix_batch_size = remaining_data_points
            else:
                remaining_data_points = fix_batch_size
            while remaining_data_points > 0:
                # Adjust batch size dynamically based on remaining data points
                batch_size = min(remaining_data_points, max_batch_size)
                start_idx = (
                    (fix_batch_size - remaining_data_points)
                    + 1
                    + (fix_batch_size * itr)
                )
                end_idx = start_idx + batch_size
                keys_to_extract = list(range(start_idx, end_idx))
                ins_subset = {
                    i + 1: ins[str(key)]
                    for i, key in enumerate(keys_to_extract)
                    if str(key) in ins
                }

                if few_shots_per_datapoint:
                    # print("few_shots_per_datapoint",few_shots_per_datapoint.keys())
                    few_shot_subset = {
                        i + 1: few_shots_per_datapoint[str(key)]
                        for i, key in enumerate(keys_to_extract)
                        if str(key) in few_shots_per_datapoint
                    }
                    # print("subset", few_shot_subset)

                plan_details["Batch Instruction"] = BATCH_PROMPT.format(
                    batch_try=batch_try
                )
                for i in ins_subset:
                    realism_text = """
CRITICAL REALISM REQUIREMENTS:
- Generate data that reflects authentic human communication and behavior
- Include natural imperfections: realistic typos, informal language, emotional expressions
- Use specific, realistic details instead of generic placeholders (NO "user123", "example@email.com", "Lorem ipsum")
- Vary communication styles: formal, casual, technical, emotional, rushed, thoughtful
- Add cultural authenticity and personal touches where appropriate
- Include natural human inconsistencies and varying expertise levels
- Make conversations feel genuine with interruptions, clarifications, and natural flow
- Ensure all fields contain meaningful, non-empty, contextually appropriate content

INSTRUCTION: """ + str(ins_subset[i])
                    plan_details["Batch Instruction"] += realism_text
                    if few_shots_per_datapoint:
                        # Extract only the chunk_text from each few shot example and store as a list
                        for fs_key, fs_list in few_shot_subset.items():
                            chunk_texts = []
                            for fs in fs_list:
                                if "chunk_text" in fs:
                                    chunk_texts.append(fs["chunk_text"])
                                else:
                                    # Keep the original item if chunk_text is not available
                                    chunk_texts.append(fs)

                            # Replace the original few_shot_subset with just the list of chunk_texts
                            few_shot_subset[fs_key] = chunk_texts
                        plan_details["Batch Instruction"] += (
                            f"\n\nDomain Knowledge for this Datapoint:\n"
                            + "\n".join([str(fs) for fs in few_shot_subset[i]])
                        )
                # print("PLAN_DETAILS",plan_details)
                try:
                    generated_batch = self._generate_data_for_plan(
                        plan_details,
                        params,
                        dynamic_batch_size=batch_size,
                        seed=random.randint(0, 1000),  # Random seed for variation
                    )

                    if generated_batch is None:
                        logger.error(
                            f"_generate_data_for_plan returned None for plan {plan_id}, batch size {batch_size}."
                        )
                        raise ValueError(
                            "Generated batch is None. Please check the input parameters."
                        )

                    if "error" in generated_batch:
                        logger.error(
                            f"Error in generating data for plan {plan_id}: {generated_batch['error']['message']}"
                        )
                        raise ValueError(
                            f"Error in generating data: {generated_batch['error']['message']}"
                        )

                    # Validate and repair the generated batch
                    validated_batch = self._validate_and_repair_batch(
                        generated_batch, params
                    )
                    try:
                        # Add validated data to plan_data while filtering out metadata entries
                        fallback_text = "Could not generate this datapoint"
                        schema_definitions = params.get("schema", {}) or {}
                        schema_fields = list(schema_definitions.keys())
                        records_to_add: List[Dict[str, Any]] = []

                        if isinstance(validated_batch, dict):
                            digit_key_records = {
                                str(key): value
                                for key, value in validated_batch.items()
                                if str(key).isdigit()
                            }
                            source_iterable = (
                                digit_key_records.values()
                                if digit_key_records
                                else [validated_batch]
                            )
                        elif isinstance(validated_batch, list):
                            source_iterable = validated_batch
                        else:
                            source_iterable = [validated_batch]

                        for item in source_iterable:
                            if not isinstance(item, dict):
                                continue
                            record = item.copy()
                            replaced_all_with_placeholder = True
                            for field in schema_fields:
                                value = record.get(field)
                                if value is None or (
                                    isinstance(value, str) and value.strip() == ""
                                ):
                                    schema_type = (
                                        schema_definitions.get(field) or {}
                                    ).get("type", "")
                                    if schema_type in {"json", "object", "dict"}:
                                        record[field] = {}
                                    elif schema_type in {"array", "list"}:
                                        record[field] = []
                                    else:
                                        record[field] = fallback_text
                                if record.get(field) != fallback_text:
                                    replaced_all_with_placeholder = False
                            if schema_fields and replaced_all_with_placeholder:
                                continue
                            records_to_add.append(record)

                        plan_data.extend(records_to_add)
                        actual_added = len(records_to_add)
                        remaining_data_points -= (
                            actual_added if actual_added > 0 else batch_size
                        )
                        batch_try += 1
                    except Exception as e:
                        logger.exception(
                            f"Error processing validated batch: {validated_batch}, with exception {e}. Batch size: {batch_size}"
                        )
                except Exception as e:
                    logger.exception(
                        f"Error generating data with batch size {batch_size}: {e}. Reducing batch size."
                    )
                    if batch_size <= 1:
                        logger.error(
                            "Error: Unable to generate valid data even with batch size 1. Please increase token limit."
                        )
                        break
                    max_batch_size -= 1

        plan_data_dict: Dict[int, Any] = dict(
            zip(range(1, datapoints_per_plan + 1), plan_data)
        )
        all_generated_data["plans"][f"itr_{retry_count}_{plan_id}"] = {
            "plan_details": plan_details,
            "generated_data": plan_data_dict,
        }
        if dataset_id:
            current_idx = 0  # Initialize idx
            idx = 0  # Initialize idx
            for datapoint in plan_data_dict.values():
                rows[idx]["cells"] = [
                    {
                        "column_name": column_name,
                        "value": datapoint[column_name],
                    }
                    for column_name in payload["schema"].keys()
                ]
                idx += 1
            # self._add_rows_to_dataset(
            #     rows[current_idx:idx], dataset_id, headers, base_url
            # )
        # replace NaN values with "There was an error generating this data point"
        for data_id, data_point in plan_data_dict.items():
            for column_name, value in data_point.items():
                if value is None or (isinstance(value, str) and value.strip() == ""):
                    data_point[column_name] = (
                        "There was an error generating this data point"
                    )
        return (
            plan_id,
            {"plan_details": plan_details, "generated_data": plan_data_dict},
            chunk_ids,
        )

    async def _generate_plan_data_async(
        self,
        plan_info: Tuple[int, str, Dict[str, Any]],
        retry_count: int,
        payload: Dict,
        seed_agent,
        all_generated_data,
        dataset_id,
        headers,
        base_url,
        params: Dict,
        dp_per_plan_list: list,
        rows=None,
        called_for=None,
    ) -> Tuple[str, Dict, Set[Any]]:
        chunk_ids: Set[Any] = set()
        i, plan_id, plan_details = plan_info
        datapoints_per_plan = dp_per_plan_list[i]
        number_of_iterations_per_plan = math.ceil(datapoints_per_plan / 5)
        ins = await self.get_ins_async(payload, plan_details, datapoints_per_plan)
        if retry_count > 0:
            plan_details["Standing Instruction"] = (
                "Previously generated data points from this plan was discarded because semantic diversity was very low. Generate diverse datapoints this time."
            )

        plan_data: List[Any] = []
        batch_try = 1
        few_shots_per_datapoint = {}
        if "knowledge_base" in payload:
            few_shots_per_datapoint = seed_agent.fetch_few_shots_per_datapoint(
                ins, plan_details, 4
            )
            for i, few_shots in few_shots_per_datapoint.items():
                for few_shot in few_shots:
                    chunk_ids.add(few_shot["chunk_id"])
        for itr in range(number_of_iterations_per_plan):
            max_batch_size = 5
            if "knowledge_base" in payload:
                max_batch_size = 1
            fix_batch_size = 5
            if (
                itr == number_of_iterations_per_plan - 1
                and datapoints_per_plan % fix_batch_size != 0
            ):
                remaining_data_points = datapoints_per_plan % fix_batch_size
                fix_batch_size = remaining_data_points
            else:
                remaining_data_points = fix_batch_size
            while remaining_data_points > 0:
                batch_size = min(remaining_data_points, max_batch_size)
                start_idx = (
                    (fix_batch_size - remaining_data_points)
                    + 1
                    + (fix_batch_size * itr)
                )
                end_idx = start_idx + batch_size
                keys_to_extract = list(range(start_idx, end_idx))
                ins_subset = {
                    i + 1: ins[str(key)]
                    for i, key in enumerate(keys_to_extract)
                    if str(key) in ins
                }

                if few_shots_per_datapoint:
                    few_shot_subset = {
                        i + 1: few_shots_per_datapoint[str(key)]
                        for i, key in enumerate(keys_to_extract)
                        if str(key) in few_shots_per_datapoint
                    }

                plan_details["Batch Instruction"] = BATCH_PROMPT.format(
                    batch_try=batch_try
                )
                for i in ins_subset:
                    realism_text = """
CRITICAL REALISM REQUIREMENTS:
- Generate data that reflects authentic human communication and behavior
- Include natural imperfections: realistic typos, informal language, emotional expressions
- Use specific, realistic details instead of generic placeholders (NO "user123", "example@email.com", "Lorem ipsum")
- Vary communication styles: formal, casual, technical, emotional, rushed, thoughtful
- Add cultural authenticity and personal touches where appropriate
- Include natural human inconsistencies and varying expertise levels
- Make conversations feel genuine with interruptions, clarifications, and natural flow
- Ensure all fields contain meaningful, non-empty, contextually appropriate content

INSTRUCTION: """ + str(ins_subset[i])
                    plan_details["Batch Instruction"] += realism_text
                    if few_shots_per_datapoint:
                        for fs_key, fs_list in few_shot_subset.items():
                            chunk_texts = []
                            for fs in fs_list:
                                if "chunk_text" in fs:
                                    chunk_texts.append(fs["chunk_text"])
                                else:
                                    chunk_texts.append(fs)

                            few_shot_subset[fs_key] = chunk_texts
                        plan_details["Batch Instruction"] += (
                            f"\n\nDomain Knowledge for this Datapoint:\n"
                            + "\n".join([str(fs) for fs in few_shot_subset[i]])
                        )
                try:
                    generated_batch = await self._generate_data_for_plan_async(
                        plan_details,
                        params,
                        dynamic_batch_size=batch_size,
                        seed=random.randint(0, 1000),
                    )

                    if generated_batch is None:
                        logger.error(
                            f"_generate_data_for_plan returned None for plan {plan_id}, batch size {batch_size}."
                        )
                        raise ValueError(
                            "Generated batch is None. Please check the input parameters."
                        )

                    if "error" in generated_batch:
                        logger.error(
                            f"Error in generating data for plan {plan_id}: {generated_batch['error']['message']}"
                        )
                        raise ValueError(
                            f"Error in generating data: {generated_batch['error']['message']}"
                        )

                    validated_batch = await self._validate_and_repair_batch_async(
                        generated_batch, params
                    )
                    try:
                        fallback_text = "Could not generate this datapoint"
                        schema_definitions = params.get("schema", {}) or {}
                        schema_fields = list(schema_definitions.keys())
                        records_to_add: List[Dict[str, Any]] = []

                        if isinstance(validated_batch, dict):
                            digit_key_records = {
                                str(key): value
                                for key, value in validated_batch.items()
                                if str(key).isdigit()
                            }
                            source_iterable = (
                                digit_key_records.values()
                                if digit_key_records
                                else [validated_batch]
                            )
                        elif isinstance(validated_batch, list):
                            source_iterable = validated_batch
                        else:
                            source_iterable = [validated_batch]

                        for item in source_iterable:
                            if not isinstance(item, dict):
                                continue
                            record = item.copy()
                            replaced_all_with_placeholder = True
                            for field in schema_fields:
                                value = record.get(field)
                                if value is None or (
                                    isinstance(value, str) and value.strip() == ""
                                ):
                                    schema_type = (
                                        schema_definitions.get(field) or {}
                                    ).get("type", "")
                                    if schema_type in {"json", "object", "dict"}:
                                        record[field] = {}
                                    elif schema_type in {"array", "list"}:
                                        record[field] = []
                                    else:
                                        record[field] = fallback_text
                                if record.get(field) != fallback_text:
                                    replaced_all_with_placeholder = False
                            if schema_fields and replaced_all_with_placeholder:
                                continue
                            records_to_add.append(record)

                        plan_data.extend(records_to_add)
                        actual_added = len(records_to_add)
                        remaining_data_points -= (
                            actual_added if actual_added > 0 else batch_size
                        )
                        batch_try += 1
                    except Exception as e:
                        logger.exception(
                            f"Error processing validated batch: {validated_batch}, with exception {e}. Batch size: {batch_size}"
                        )
                except Exception as e:
                    logger.exception(
                        f"Error generating data with batch size {batch_size}: {e}. Reducing batch size."
                    )
                    if batch_size <= 1:
                        logger.error(
                            "Error: Unable to generate valid data even with batch size 1. Please increase token limit."
                        )
                        break
                    max_batch_size -= 1

        plan_data_dict: Dict[int, Any] = dict(
            zip(range(1, datapoints_per_plan + 1), plan_data)
        )
        if dataset_id:
            current_idx = 0
            idx = 0
            for datapoint in plan_data_dict.values():
                rows[idx]["cells"] = [
                    {
                        "column_name": column_name,
                        "value": datapoint[column_name],
                    }
                    for column_name in payload["schema"].keys()
                ]
                idx += 1
        for data_id, data_point in plan_data_dict.items():
            for column_name, value in data_point.items():
                if value is None or (isinstance(value, str) and value.strip() == ""):
                    data_point[column_name] = (
                        "There was an error generating this data point"
                    )
        return (
            plan_id,
            {"plan_details": plan_details, "generated_data": plan_data_dict},
            chunk_ids,
        )

    def _generate_data_for_plan(self, plan_details, params, dynamic_batch_size, seed=0):
        """Generate data using a specific plan"""
        # Default assignment if no modifications are needed
        plan_details_copy = plan_details

        # Remove 'Datapoints' key if using a knowledge base
        if (
            isinstance(plan_details, dict)
            and "Datapoints" in plan_details
            and "knowledge_base" in params
        ):
            # Create a copy of plan_details to avoid modifying the original
            plan_details_copy = plan_details.copy()
            plan_details_copy.pop("Datapoints", None)

        generation_prompt = GENERATION_PROMPT.format(
            plan=json.dumps(plan_details_copy, indent=2, ensure_ascii=False),
            requirements=params["requirements"],
            schema=json.dumps(params["schema"], indent=2, ensure_ascii=False),
            constraints=json.dumps(params["constraints"], indent=2, ensure_ascii=False),
            batch_size=dynamic_batch_size,
            seed=seed,
        )

        try:
            if not self.should_continue():
                # optionally attach context/payload
                raise AbortFlow("Stopped because check failed")
            response = self.llm._get_completion_content(
                messages=[{"role": "user", "content": generation_prompt}],
                response_format={"type": "json_object"},
            )
            # logger.info(f"LLM raw response for data generation (seed {seed}) generated")

            # If the response is already a dictionary, use it directly
            if isinstance(response, dict):
                answers = response
            else:
                # Attempt to clean and parse the response if it's a string
                cleaned_response = re.sub(r"//.*", "", str(response))
                cleaned_response = cleaned_response.strip()
                if not cleaned_response.startswith("{"):
                    start_idx = cleaned_response.find("{")
                    if start_idx != -1:
                        cleaned_response = cleaned_response[start_idx:]
                if not cleaned_response.endswith("}"):
                    end_idx = cleaned_response.rfind("}")
                    if end_idx != -1:
                        cleaned_response = cleaned_response[: end_idx + 1]

                # logger.info(
                #     f"LLM cleaned response for data generation (seed {seed}) generated"
                # )

                try:
                    answers = json.loads(cleaned_response)
                except json.JSONDecodeError as e:
                    # logger.error(
                    #     f"JSONDecodeError after cleaning for seed {seed}: {e}. Attempting repair."
                    # )
                    # Try to repair the JSON using the repair agent
                    return self._fix_json_response_generations(cleaned_response)

            result_type = self.check_answers(answers)
            if result_type is False:
                logger.warning(
                    f"Generated data for seed {seed} is completely empty or NaN. Retrying..."
                )
                batch_retry = 0
                while not result_type and batch_retry < 3:
                    logger.warning(f"Retry {batch_retry + 1} for seed {seed}...")
                    seed += 1
                    retry_response = self._generate_data_for_plan(
                        plan_details, params, dynamic_batch_size, seed
                    )
                    if retry_response is not None:
                        if isinstance(retry_response, dict):
                            answers = retry_response
                        else:
                            try:
                                answers = json.loads(retry_response)
                            except json.JSONDecodeError:
                                answers = {}  # Fallback if retry response is also bad JSON
                        result_type = self.check_answers(answers)
                    else:
                        result_type = False  # Still None, so still bad
                    batch_retry += 1
                if result_type is False:
                    (
                        f"Failed to generate valid data after {batch_retry} retries for seed {seed}."
                    )
                    return None
            return answers

        except Exception as e:
            logger.exception(
                f"Error generating data for plan with seed {seed}: {str(e)}"
            )
            return None

    async def _generate_data_for_plan_async(
        self, plan_details, params, dynamic_batch_size, seed=0
    ):
        """Generate data using a specific plan asynchronously"""
        plan_details_copy = plan_details

        if (
            isinstance(plan_details, dict)
            and "Datapoints" in plan_details
            and "knowledge_base" in params
        ):
            plan_details_copy = plan_details.copy()
            plan_details_copy.pop("Datapoints", None)

        generation_prompt = GENERATION_PROMPT.format(
            plan=json.dumps(plan_details_copy, indent=2, ensure_ascii=False),
            requirements=params["requirements"],
            schema=json.dumps(params["schema"], indent=2, ensure_ascii=False),
            constraints=json.dumps(params["constraints"], indent=2, ensure_ascii=False),
            batch_size=dynamic_batch_size,
            seed=seed,
        )

        try:
            if not await self.should_continue_async():
                raise AbortFlow("Stopped because check failed")
            response = await self.llm._get_completion_content_async(
                messages=[{"role": "user", "content": generation_prompt}],
                response_format={"type": "json_object"},
            )
            # logger.info(f"LLM raw response for data generation (seed {seed}) generated")

            if isinstance(response, dict):
                answers = response
            else:
                cleaned_response = re.sub(r"//.*", "", str(response))
                cleaned_response = cleaned_response.strip()
                if not cleaned_response.startswith("{"):
                    start_idx = cleaned_response.find("{")
                    if start_idx != -1:
                        cleaned_response = cleaned_response[start_idx:]
                if not cleaned_response.endswith("}"):
                    end_idx = cleaned_response.rfind("}")
                    if end_idx != -1:
                        cleaned_response = cleaned_response[: end_idx + 1]

                # logger.info(
                #     f"LLM cleaned response for data generation (seed {seed}) generated"
                # )

                try:
                    answers = json.loads(cleaned_response)
                except json.JSONDecodeError as e:
                    # logger.error(
                    #     f"JSONDecodeError after cleaning for seed {seed}: {e}. Attempting repair."
                    # )
                    return await self._fix_json_response_generations_async(
                        cleaned_response
                    )

            result_type = self.check_answers(answers)
            if result_type is False:
                logger.warning(
                    f"Generated data for seed {seed} is completely empty or NaN. Retrying..."
                )
                batch_retry = 0
                while not result_type and batch_retry < 3:
                    logger.warning(f"Retry {batch_retry + 1} for seed {seed}...")
                    seed += 1
                    retry_response = await self._generate_data_for_plan_async(
                        plan_details, params, dynamic_batch_size, seed
                    )
                    if retry_response is not None:
                        if isinstance(retry_response, dict):
                            answers = retry_response
                        else:
                            try:
                                answers = json.loads(retry_response)
                            except json.JSONDecodeError:
                                answers = {}
                        result_type = self.check_answers(answers)
                    else:
                        result_type = False
                    batch_retry += 1
                if result_type is False:
                    logger.error(
                        f"Failed to generate valid data after {batch_retry} retries for seed {seed}."
                    )
                    return None
            return answers

        except Exception as e:
            logger.exception(
                f"Error generating data for plan with seed {seed}: {str(e)}"
            )
            return None

    def prepare_payload(self, payload):
        prompt_name = payload.get("prompt_name", "")
        prompt_instruction = payload.get("prompt_instructions", "")
        # print("prompt_instruction", prompt_instruction)
        variable_names = payload.get("variable_names", [])
        batch_size = payload.get("batch_size", 1)
        converted_variables = []
        space_map = {}
        for var in variable_names:
            new_var = re.sub(r"\s+", "_", var)
            space_map[new_var] = var
            converted_variables.append(new_var)

        content = []
        content.append(
            {
                "type": "text",
                "text": PREPARE_PAYLOAD_PROMPT.format(
                    prompt_name=prompt_name,
                    prompt_instruction=prompt_instruction,
                    variable_names=converted_variables,
                ),
            }
        )
        if not self.should_continue():
            # optionally attach context/payload
            raise AbortFlow("Stopped because check failed")
        ins_response = self.llm._get_completion_content(
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
        )
        try:
            payload = json.loads(ins_response)
            # payload["batch_size"] = 10
            for constraint in payload["constraints"]:
                # Get current value of the "field"
                field_value = constraint["field"]

            field_names = [constraint["field"] for constraint in payload["constraints"]]
            if field_names != converted_variables:
                raise ValueError(
                    "Cannot generate data at this time please try again later."
                )
            schema = {
                constraint["field"]: {"type": constraint["type"]}
                for constraint in payload["constraints"]
            }
            payload["schema"] = schema
            payload["batch_size"] = batch_size
            return payload, space_map

        except json.JSONDecodeError:
            raise ValueError(
                "Cannot generate data at this time please try again later."
            )

    async def prepare_payload_async(self, payload):
        prompt_name = payload.get("prompt_name", "")
        prompt_instruction = payload.get("prompt_instructions", "")
        variable_names = payload.get("variable_names", [])
        batch_size = payload.get("batch_size", 1)
        converted_variables = []
        space_map = {}
        for var in variable_names:
            new_var = re.sub(r"\s+", "_", var)
            space_map[new_var] = var
            converted_variables.append(new_var)

        content = []
        content.append(
            {
                "type": "text",
                "text": PREPARE_PAYLOAD_PROMPT.format(
                    prompt_name=prompt_name,
                    prompt_instruction=prompt_instruction,
                    variable_names=converted_variables,
                ),
            }
        )
        if not await self.should_continue_async():
            # optionally attach context/payload
            raise AbortFlow("Stopped because check failed")
        ins_response = await self.llm._get_completion_content_async(
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
        )
        try:
            payload = json.loads(ins_response)
            for constraint in payload["constraints"]:
                field_value = constraint["field"]

            field_names = [constraint["field"] for constraint in payload["constraints"]]
            if field_names != converted_variables:
                raise ValueError(
                    "Cannot generate data at this time please try again later."
                )
            schema = {
                constraint["field"]: {"type": constraint["type"]}
                for constraint in payload["constraints"]
            }
            payload["schema"] = schema
            payload["batch_size"] = batch_size
            return payload, space_map

        except json.JSONDecodeError:
            raise ValueError(
                "Cannot generate data at this time please try again later."
            )

    def _normalize_instruction_map(self, raw_instructions: Any) -> Dict[str, Any]:
        """
        Normalize LLM instruction output into a dict with numeric string keys ("1", "2", ...).

        The prompt requests a JSON object, but models sometimes return a list or use integer keys.
        Downstream batching logic expects string keys.
        """
        if isinstance(raw_instructions, dict):
            if "instructions" in raw_instructions and isinstance(
                raw_instructions.get("instructions"), list
            ):
                raw_instructions = raw_instructions["instructions"]
            else:
                return {str(k): v for k, v in raw_instructions.items()}

        if isinstance(raw_instructions, list):
            normalized: Dict[str, Any] = {}
            for idx, item in enumerate(raw_instructions, start=1):
                if isinstance(item, dict) and len(item) == 1:
                    only_key = next(iter(item.keys()))
                    if isinstance(only_key, int) or (
                        isinstance(only_key, str) and only_key.isdigit()
                    ):
                        normalized[str(only_key)] = item[only_key]
                        continue
                normalized[str(idx)] = item
            return normalized

        return {"1": raw_instructions}

    def get_ins(self, payload, plan, datapoints_per_plan):
        content, params = self._get_prompt(payload)
        # params
        content.append(
            {
                "type": "text",
                "text": INS_PROMPT.format(
                    plan=plan, datapoints_per_plan=datapoints_per_plan, **params
                ),
            }
        )
        if not self.should_continue():
            # optionally attach context/payload
            raise AbortFlow("Stopped because check failed")
        ins_response = self.llm._get_completion_content(
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
        )

        try:
            ins_response = ins_response.strip()
            if not ins_response.startswith("{"):
                # Try to find the first valid JSON object
                start_idx = ins_response.find("{")
                if start_idx != -1:
                    ins_response = ins_response[start_idx:]
            if not ins_response.endswith("}"):
                # Try to find the last valid JSON object
                end_idx = ins_response.rfind("}")
                if end_idx != -1:
                    ins_response = ins_response[: end_idx + 1]
            # print("ins_response after removing extra text: ")
            ins = json_repair.loads(ins_response)
        except json.JSONDecodeError:
            logger.error(
                "Error: Failed to parse Instructions into JSON. Attempting to fix ..."
            )
            ins = self._fix_json_response_ins(ins_response)
        return self._normalize_instruction_map(ins)

    async def get_ins_async(self, payload, plan, datapoints_per_plan):
        content, params = self._get_prompt(payload)
        content.append(
            {
                "type": "text",
                "text": INS_PROMPT.format(
                    plan=plan, datapoints_per_plan=datapoints_per_plan, **params
                ),
            }
        )
        if not await self.should_continue_async():
            # optionally attach context/payload
            raise AbortFlow("Stopped because check failed")
        ins_response = await self.llm._get_completion_content_async(
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
        )

        try:
            ins_response = ins_response.strip()
            if not ins_response.startswith("{"):
                # Try to find the first valid JSON object
                start_idx = ins_response.find("{")
                if start_idx != -1:
                    ins_response = ins_response[start_idx:]
            if not ins_response.endswith("}"):
                # Try to find the last valid JSON object
                end_idx = ins_response.rfind("}")
                if end_idx != -1:
                    ins_response = ins_response[: end_idx + 1]
            ins = json_repair.loads(ins_response)
        except json.JSONDecodeError:
            logger.error(
                "Error: Failed to parse Instructions into JSON. Attempting to fix ..."
            )
            ins = await self._fix_json_response_ins_async(ins_response)
        return self._normalize_instruction_map(ins)

    def prepare_final_dataframe(self, holistic_results, user_input_columns):
        """
        Prepare the final DataFrame with only user-specified columns and generated values.

        Args:
            holistic_results (dict): The dictionary containing the final generated data.
            user_input_columns (list): List of column names provided by the user.

        Returns:
            pd.DataFrame: A DataFrame containing only the user-specified columns and generated values.
        """
        if not isinstance(user_input_columns, list):
            user_input_columns = list(user_input_columns)

        # check if we get plan branch names key, if not, we dont need to create the column.
        branch_lookup: Dict[str, Optional[str]] = holistic_results.get(
            "plan_branch_names", {}
        )
        include_branch_name = bool(branch_lookup)

        logger.info(
            f"Preparing final dataframe. Received {len(holistic_results.get('plans', {}))} plans."
        )
        # Extract all generated data across plans
        all_data = []
        for plan_id, plan_data in holistic_results.get("plans", {}).items():
            generated_data = plan_data.get("generated_data", {})
            logger.info(f"Processing plan {plan_id}: {len(generated_data)} data points")
            for data_point in generated_data.values():
                # Extract only the user-specified columns
                filtered_data = {
                    col: data_point.get(col, None) for col in user_input_columns
                }
                # add the branch names under the branch_name column name.
                if include_branch_name:
                    branch_name = branch_lookup.get(plan_id)
                    if not branch_name:
                        original_plan_id = data_point.get("original_plan_id")
                        if original_plan_id:
                            branch_name = branch_lookup.get(original_plan_id)
                    filtered_data["branch_name"] = branch_name
                all_data.append(filtered_data)

        # Convert to a DataFrame
        logger.info(f"Processed {len(all_data)} total data points into a list.")
        final_df = pd.DataFrame(all_data)

        # Ensure columns are ordered as per the user's input
        columns_order = list(user_input_columns)
        if include_branch_name and "branch_name" not in columns_order:
            columns_order.append("branch_name")
        final_df = final_df[columns_order]
        logger.info(
            f"Final DataFrame prepared with shape: {final_df.shape} and columns: {final_df.columns.tolist()}"
        )

        return final_df

    def _sample_reference_data(self, payload):
        """
        Samples the reference_data in the payload if it's larger than 20 rows.
        It takes a 10% sample, with a minimum of 20 records.
        """
        if "reference_data" not in payload or not payload["reference_data"]:
            return payload

        try:
            df = pd.DataFrame(payload["reference_data"])
            original_size = len(df)
            sampling_threshold = 20

            if original_size <= sampling_threshold:
                # Don't sample if the dataset is already small
                return payload

            # Calculate sample size: 10% of the data, but at least 20 records
            sample_size = min(30, max(sampling_threshold, int(original_size * 0.1)))

            # logger.info(
            #     f"Reference data size ({original_size}) is large. "
            #     f"Sampling down to {sample_size} records for processing."
            # )

            sampled_df = df.sample(n=sample_size, random_state=42)

            # Replace the original full data with the new smaller sample
            payload["reference_data"] = sampled_df.to_dict(orient="records")

        except Exception as e:
            logger.error(
                f"Error during reference data sampling: {e}. Using original data to proceed."
            )

        return payload

    def _adjust_data_distribution(
        self, params, generated_data, category_column, expected_distribution
    ):
        """
        Adjust data distribution by generating specific numbers of datapoints per category and removing excess data.

        Args:
            generated_data (dict): The generated data to adjust.
            category_column (str): The categorical column to adjust.
            expected_distribution (dict): The desired distribution.

        Returns:
            dict: Adjusted generated data.
        """
        category_counts = defaultdict(list)

        # Organize data by category
        for data_id, data_point in generated_data.items():
            category = data_point.get(category_column)
            if category is not None:
                category_counts[category].append((data_id, data_point))

        total_samples = sum(len(items) for items in category_counts.values())

        adjusted_data = {}
        next_id = max(map(int, generated_data.keys())) + 1 if generated_data else 1

        for category, target_proportion in expected_distribution.items():
            target_count = int(target_proportion * total_samples)
            current_count = len(category_counts.get(category, []))

            if current_count > target_count:
                # Remove excess samples randomly
                retained_samples = random.sample(
                    category_counts[category], target_count
                )
                category_counts[category] = retained_samples
            elif current_count < target_count:
                # Keep existing samples and calculate missing count

                missing_count = target_count - current_count
                few_shot_examples = (
                    random.sample(
                        category_counts.get(category, []), min(4, current_count)
                    )
                    if current_count > 0
                    else []
                )

                # Generate new samples
                new_samples = self._generate_category_samples(
                    params,
                    category,
                    missing_count,
                    [item[1] for item in few_shot_examples],
                )

                # Add new samples to the category
                for sample in new_samples:
                    sample["original_plan_id"] = (
                        "new_class_updated_data"  # Mark new samples with no original plan
                    )
                    category_counts[category].append((str(next_id), sample))
                    next_id += 1

            # Add final samples to adjusted data
            for data_id, sample in category_counts[category]:
                # sample["original_plan_id"] = generated_data[data_id]["original_plan_id"] if data_id in generated_data else None
                adjusted_data[data_id] = sample

        return adjusted_data

    def _generate_category_samples(
        self,
        params,
        category,
        count,
        few_shot_examples,
    ):
        """
        Generate new data samples for a specific category using few-shot examples.

        Args:
            category (str): The category for which data needs to be generated.
            count (int): Number of new samples to generate.
            few_shot_examples (list): Few-shot examples to guide the generation.

        Returns:
            list: Generated samples.
        """

        remaining_data_points = count
        plan_data: List[Any] = []
        while remaining_data_points > 0:
            # Adjust batch size dynamically based on remaining data points
            batch_size = min(remaining_data_points, 5)
            completed_points = 0
            org_batch_size = batch_size
            batch_try = 1
            while completed_points != org_batch_size:
                formatted_examples = "\n".join(
                    str(example) for example in few_shot_examples
                )
                plan_details = {
                    "Standing Instruction": f"Generate datapoints for class {category}. Use the following as reference datapoints: \n {formatted_examples}"
                }
                plan_details["Batch Instruction"] = BATCH_PROMPT.format(
                    batch_try=batch_try
                )
                try:
                    generated_batch = self._generate_data_for_plan(
                        plan_details,
                        params,
                        dynamic_batch_size=batch_size,
                        seed=random.randint(0, 1000),  # Random seed for variation
                    )
                    if generated_batch:
                        # Add generated data to plan_data
                        plan_data.extend(
                            generated_batch[str(j)] for j in range(1, batch_size + 1)
                        )
                        completed_points += batch_size
                        batch_try += 1
                    else:
                        raise ValueError("Generated batch is None")
                except Exception as e:
                    logger.error(
                        f"Error generating data with batch size {batch_size}: {e}. Reducing batch size."
                    )
                    if batch_size <= 1:
                        logger.error(
                            "Error: Unable to generate valid data even with batch size 1. Please increase token limit."
                        )
                        break
                    batch_size -= 1
            remaining_data_points -= completed_points

        return plan_data

    def _get_prompt(self, payload):
        schema = payload.get("schema", {})
        requirements = payload.get("requirements", "")
        constraints = payload.get("constraints", [])
        batch_size = payload.get("batch_size", 100)
        # distribution_params = payload.get("distribution_params", {})
        # logger.info(f"Sampling reference_data to reduce prompt content...")
        # use deepcopy to avoid affecting the original payload
        payload_copy = copy.deepcopy(payload)
        payload_copy = self._sample_reference_data(payload_copy)
        # Format the content for the LLM
        content = []

        # Add any reference data if provided
        if "reference_data" in payload_copy:
            # logger.info("Adding reference data to prompt content.")
            content.append({"type": "text", "text": "<reference_data>"})
            content.append(
                {
                    "type": "text",
                    "text": json.dumps(
                        payload_copy["reference_data"], indent=2, ensure_ascii=False
                    ),
                }
            )
            content.append({"type": "text", "text": "</reference_data>"})

        params = {
            "requirements": requirements,
            "schema": schema,
            "constraints": constraints,
            "batch_size": batch_size,
            # "distribution_params": distribution_params
        }
        return content, params

    def _default_quality_thresholds(self):
        """Define default quality thresholds for data validation"""
        return {
            "completeness": 0.99,
            "consistency": 0.95,
            "distribution_p_value": 0.05,
            "correlation_threshold": 0.8,
            "anomaly_rate": 0.01,
        }

    def normalize_text(self, text):
        if getattr(type(self), "_tokenizer", None) is None:
            from transformers import AutoTokenizer

            try:
                type(self)._tokenizer = AutoTokenizer.from_pretrained(
                    "bert-base-uncased",
                    strip_accents=True,
                    lowercase=True,
                    local_files_only=True,
                )
            except Exception:
                type(self)._tokenizer = AutoTokenizer.from_pretrained(
                    "bert-base-uncased", strip_accents=True, lowercase=True
                )
        tokens = type(self)._tokenizer.tokenize(text)
        return " ".join(tokens)

    def compute_overall_distribution_stats(
        self, all_generated_data, category_column, expected_distribution=None
    ):
        """
        Compute and evaluate the overall distribution of a specified categorical column across all plans.

        Args:
            all_generated_data (dict): The dictionary containing all generated data.
            category_column (str): The name of the categorical column to evaluate.
            expected_distribution (dict, optional): Expected distribution of categories.
                Example: {"category_1": 0.4, "category_2": 0.6}

        Returns:
            dict: Overall distribution statistics including observed distribution, expected distribution, and chi-square test results.
        """
        all_values = []

        for plan_data in all_generated_data["plans"].values():
            generated_data = plan_data.get("generated_data", {})
            all_values.extend(
                [
                    item.get(category_column)
                    for item in generated_data.values()
                    if category_column in item
                ]
            )

        if not all_values:
            logger.warning(
                f"Column '{category_column}' not found in any plan data for distribution stats."
            )
            return {"error": f"Column '{category_column}' not found in any plan data."}

        # Compute observed distribution
        unique_values, counts = np.unique(all_values, return_counts=True)
        observed_distribution = dict(zip(unique_values, counts / sum(counts)))
        # Use expected distribution or assume uniform if not provided
        if expected_distribution:
            expected_dist = np.array(
                [
                    expected_distribution.get(self.normalize_text(val), 0)
                    for val in unique_values
                ]
            )
            expected_dist /= (
                expected_dist.sum()
            )  # Normalize to ensure it's a valid distribution
        else:
            expected_dist = np.ones_like(counts) / len(counts)  # Uniform distribution

        total_count = sum(counts)

        # Perform chi-square test (lazy import for faster startup)
        from scipy.stats import chisquare

        chi2_stat, chi2_p_value = chisquare(
            f_obs=counts, f_exp=expected_dist * sum(counts)
        )

        return {
            "observed_distribution": observed_distribution,
            "expected_distribution": dict(zip(unique_values, expected_dist)),
            "chi2_stat": chi2_stat,
            "chi2_p_value": chi2_p_value,
            "is_uniform": chi2_p_value
            > 0.05,  # Null hypothesis: no significant difference
        }

    def _compute_mad_threshold(
        self, values: List[float], mad_multiplier: float = 2.5
    ) -> Tuple[float, float, float]:
        """
        Compute MAD-based outlier threshold for upper tail detection.

        Uses Median Absolute Deviation (MAD) which is robust against outliers,
        unlike standard deviation. Only flags values in the upper tail as outliers
        (high similarity = bad for diversity).

        Args:
            values: List of similarity values
            mad_multiplier: Number of MADs from median to consider outlier.
                           2.5 = moderate (catches clear outliers, ~1-2% false positive)

        Returns:
            Tuple of (median, mad, threshold)
            If MAD=0, returns (median, 0, infinity) to skip the check
        """
        if not values:
            return (0.0, 0.0, float("inf"))

        values_arr = np.array(values)
        median_val = float(np.median(values_arr))

        # MAD = median of absolute deviations from median
        abs_deviations = np.abs(values_arr - median_val)
        mad = float(np.median(abs_deviations))

        if mad == 0:
            # All values are identical or nearly so - skip outlier detection
            return (median_val, 0.0, float("inf"))

        # Consistency constant 1.4826 makes MAD comparable to std dev for normal dist
        # Upper threshold only (we only care about HIGH similarities being bad)
        threshold = median_val + (mad_multiplier * 1.4826 * mad)

        return (median_val, mad, threshold)

    def _ensure_embeddings_cached(self, texts: List[str]) -> bool:
        """
        Ensure all texts have embeddings in the cache.

        Args:
            texts: List of text strings to embed

        Returns:
            True if successful, False if embedding failed
        """
        if not hasattr(self, "_str2emb"):
            self._str2emb: Dict[str, Any] = {}

        new_texts = [t for t in texts if t not in self._str2emb]
        if not new_texts:
            return True

        model = self.embedding_model
        if get_embedding_model(input_type="check_serving"):
            # For serving client function
            try:
                vecs = model(new_texts)
            except Exception as e:
                logger.exception(f"Embedding error: {e}")
                return False
        else:
            # For local model
            vecs = model.encode(
                new_texts,
                batch_size=64,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        self._str2emb.update(zip(new_texts, vecs))
        return True

    def _get_embeddings(self, texts: List[str]) -> Optional[np.ndarray]:
        """
        Get embeddings for a list of texts, ensuring they are cached first.

        Args:
            texts: List of text strings

        Returns:
            Stacked numpy array of embeddings, or None if failed
        """
        if not self._ensure_embeddings_cached(texts):
            return None

        embeddings = [self._str2emb[t] for t in texts if t in self._str2emb]
        if not embeddings:
            return None
        return np.stack(embeddings)

    def calculate_similarity(self, set1, set2):
        if not hasattr(self, "_str2emb"):
            self._str2emb: Dict[str, Any] = {}

        # Use the shared embedding method
        E1 = self._get_embeddings(set1)
        E2 = self._get_embeddings(set2)

        if E1 is None or E2 is None:
            return {
                "intra_similarity_set1": "0.0",
                "intra_similarity_set2": "0.0",
                "inter_set_similarity": "0.0",
            }

        def _avg_upper(sim_mat: np.ndarray) -> float:
            n = sim_mat.shape[0]
            if n < 2:
                return 0.0
            # take upper triangle without diag
            return float(sim_mat[np.triu_indices(n, k=1)].mean())

        intra_similarity_set1 = _avg_upper(E1 @ E1.T)  # (n₁ × n₁) dot product
        intra_similarity_set2 = _avg_upper(E2 @ E2.T)  # (n₂ × n₂) dot product

        # Fast inter-set average similarity
        inter_set_avg_similarity = (E1 @ E2.T).mean()  # single matrix multiply

        return {
            "intra_similarity_set1": str(intra_similarity_set1),
            "intra_similarity_set2": str(intra_similarity_set2),
            "inter_set_similarity": str(inter_set_avg_similarity),
        }

    def _save_generated_data(self, data, filename, foldername):
        """Save generated data to a JSON file"""
        try:
            dir_path = "../data/"
            dir_path = os.path.join(dir_path, foldername)
            # Create the directory if it doesn't exist
            os.makedirs(dir_path, exist_ok=True)
            # Full path to the file
            file_path = os.path.join(dir_path, filename)
            with open(file_path, "w") as f:
                json.dump(data, f, indent=2)
            return file_path
        except Exception as e:
            logger.exception(f"Error saving data to {filename}: {str(e)}")

    def iterative_correction_holistic(
        self,
        params,
        all_generated_data,
        category_column,
        expected_distribution=None,
        max_iterations=3,
    ):
        """
        Iteratively correct the generated data to meet the expected overall distribution for a categorical column.

        Args:
            all_generated_data (dict): The dictionary containing all generated data.
            category_column (str): The name of the categorical column to evaluate.
            expected_distribution (dict, optional): Expected distribution of categories.
            max_iterations (int): Maximum number of correction iterations.

        Returns:
            dict: Final corrected data and logs of iterations.
        """
        iteration_logs = []
        for iteration in range(max_iterations):
            distribution_stats = self.compute_overall_distribution_stats(
                all_generated_data, category_column, expected_distribution
            )
            iteration_logs.append(
                {"iteration": iteration + 1, "distribution_stats": distribution_stats}
            )

            if distribution_stats.get("is_uniform", True):
                logger.info(
                    "The overall data meets the expected distribution. Stopping correction."
                )
                break
            # Combine all data across plans for adjustment
            combined_data = {}
            ctr = 0
            for plan_id, plan_data in all_generated_data["plans"].items():
                gen_data = plan_data.get("generated_data", {})
                for data_id, data_point in gen_data.items():
                    # Add the original plan ID to each data point
                    data_point["original_plan_id"] = plan_id
                    # Add the data point to combined_data
                    combined_data[ctr] = data_point
                    ctr += 1

            # Adjust data holistically
            corrected_data = self._adjust_data_distribution(
                params, combined_data, category_column, expected_distribution
            )

            # Redistribute corrected data
            new_plan_id = f"itr_adj_{iteration}"
            adjusted_plans: Dict[str, Dict] = defaultdict(dict)

            for data_id, data_point in corrected_data.items():
                original_plan_id = data_point.get("original_plan_id")
                if original_plan_id in all_generated_data["plans"]:
                    adjusted_plans[original_plan_id][data_id] = data_point
                else:
                    adjusted_plans[new_plan_id][data_id] = data_point
                    data_point["original_plan_id"] = new_plan_id

            # Update plans
            for plan_id, data_points in adjusted_plans.items():
                if plan_id in all_generated_data["plans"]:
                    all_generated_data["plans"][plan_id]["generated_data"] = data_points
                else:
                    all_generated_data["plans"][str(plan_id)] = {
                        "generated_data": data_points,
                        "plan_details": {"name": f"New Plan {plan_id}"},
                    }
        all_records = [
            record
            for plan in all_generated_data.get("plans", {}).values()
            for record in plan.get("generated_data", {}).values()
        ]
        if all_records:
            df = pd.DataFrame(all_records)
            df = df.where(pd.notnull(df), None)
            df.to_csv("synthetic_data.csv", index=False)
        return all_generated_data

    def diversity_evaluation(self, all_gen_data, mad_multiplier: float = 2.5):
        """
        Evaluate diversity across plans using MAD-based outlier detection.

        Uses Median Absolute Deviation (MAD) to dynamically determine which plans
        are statistical outliers (abnormally similar to others), rather than using
        an arbitrary fixed threshold. This significantly reduces unnecessary retries
        when plans are reasonably diverse.

        Optimizations:
        - Pre-computes all embeddings in a single batch (not per-pair)
        - Computes intra-similarity ONCE per plan (O(n) not O(n²))
        - Only inter-similarity is computed in pairwise loop

        Args:
            all_gen_data: Generated data containing all plans
            mad_multiplier: Number of MADs from median to consider outlier.
                           2.5 = moderate (catches clear outliers, ~1-2% false positive)
                           Higher = more lenient, Lower = more aggressive

        Returns:
            List of plan names to discard (statistical outliers only)
        """
        concatenated_sources = defaultdict(list)

        longest_column = None

        for plan_id, plan_data in all_gen_data["plans"].items():
            generated_data = plan_data.get("generated_data", {})
            first_item: Dict[str, Any] = next(iter(generated_data.values()), {})

            if (
                first_item and isinstance(first_item, dict) and not longest_column
            ):  # Only determine the column once
                longest_column = max(
                    first_item,
                    key=lambda key: (
                        len(str(first_item[key])) if first_item.get(key) else 0
                    ),
                )
                break  # Exit loop after finding the column

        # Use the determined column for all plans and items
        for plan_id, plan_data in all_gen_data["plans"].items():
            sources = []
            for item_id, item_data in plan_data.get("generated_data", {}).items():
                sources.append(str(item_data.get(longest_column, "")))
            concatenated_sources[plan_id].extend(sources)

        plan_ids = list(concatenated_sources.keys())
        num_plans = len(plan_ids)

        if num_plans < 2:
            logger.info("Less than 2 plans, skipping diversity evaluation")
            return []

        # --- OPTIMIZATION: Pre-compute all embeddings in a single batch ---
        all_texts_set: Set[str] = set()
        for texts in concatenated_sources.values():
            all_texts_set.update(texts)
        all_texts = list(all_texts_set)

        # Batch embed all texts at once using shared method
        if not self._ensure_embeddings_cached(all_texts):
            logger.error("Failed to compute embeddings, skipping diversity evaluation")
            return []

        # --- OPTIMIZATION: Build embedding matrices per plan ONCE ---
        plan_embeddings: Dict[str, Optional[np.ndarray]] = {}
        for plan_id, texts in concatenated_sources.items():
            plan_embeddings[plan_id] = self._get_embeddings(texts)

        # Helper for computing average upper triangle (intra-similarity)
        def _avg_upper_tri(sim_mat: np.ndarray) -> float:
            n = sim_mat.shape[0]
            if n < 2:
                return 0.0
            return float(sim_mat[np.triu_indices(n, k=1)].mean())

        # --- OPTIMIZATION: Compute intra-similarity ONCE per plan (O(n)) ---
        plan_intra_sims: Dict[str, float] = {}
        all_intra_sims: List[Tuple[str, str, float]] = []  # (plan_id, plan_name, sim)

        for plan_id in plan_ids:
            E = plan_embeddings.get(plan_id)
            plan_details = all_gen_data["plans"][plan_id].get("plan_details", {})
            plan_name = plan_details.get("name", plan_id)

            if E is not None and E.shape[0] > 1:
                intra_sim = _avg_upper_tri(E @ E.T)
            else:
                intra_sim = 0.0

            plan_intra_sims[plan_id] = intra_sim
            all_intra_sims.append((plan_id, plan_name, intra_sim))

        # --- Compute inter-similarity only in pairwise loop (still O(n²) but unavoidable) ---
        all_inter_sims: List[Tuple[str, str, str, str, float]] = []

        for i in range(num_plans):
            for j in range(i + 1, num_plans):
                plan_i = plan_ids[i]
                plan_j = plan_ids[j]

                E1 = plan_embeddings.get(plan_i)
                E2 = plan_embeddings.get(plan_j)

                if E1 is None or E2 is None:
                    continue

                # Inter-similarity: average similarity between all pairs across sets
                inter_sim = float((E1 @ E2.T).mean())

                plan_details_i = all_gen_data["plans"][plan_i].get("plan_details", {})
                plan_details_j = all_gen_data["plans"][plan_j].get("plan_details", {})
                plan_name_i = plan_details_i.get("name", plan_i)
                plan_name_j = plan_details_j.get("name", plan_j)

                all_inter_sims.append(
                    (plan_i, plan_j, plan_name_i, plan_name_j, inter_sim)
                )

        # Step 3: Compute MAD-based thresholds
        plan_to_discard = []

        # Intra-similarity outlier detection
        intra_values = [sim for _, _, sim in all_intra_sims]
        median_intra, mad_intra, intra_threshold = self._compute_mad_threshold(
            intra_values, mad_multiplier
        )

        if mad_intra > 0:
            for plan_id, plan_name, intra_sim in all_intra_sims:
                if intra_sim > intra_threshold:
                    plan_to_discard.append(plan_name)

        # Inter-similarity outlier detection
        inter_values = [sim for _, _, _, _, sim in all_inter_sims]
        median_inter, mad_inter, inter_threshold = self._compute_mad_threshold(
            inter_values, mad_multiplier
        )

        if mad_inter > 0:
            for plan_i, plan_j, plan_name_i, plan_name_j, inter_sim in all_inter_sims:
                if inter_sim > inter_threshold:
                    # Discard the plan with worse (higher) intra-similarity
                    intra_i = plan_intra_sims.get(plan_i, 0)
                    intra_j = plan_intra_sims.get(plan_j, 0)

                    if intra_i > intra_j:
                        plan_to_discard.append(plan_name_i)
                    else:
                        plan_to_discard.append(plan_name_j)

        plans_to_discard = list(set(plan_to_discard))
        logger.info(
            f"Diversity evaluation: {len(plans_to_discard)}/{num_plans} plans to discard "
            f"(intra: median={median_intra:.3f}, threshold={intra_threshold:.3f} | "
            f"inter: median={median_inter:.3f}, threshold={inter_threshold:.3f})"
        )
        return plans_to_discard

    def _increase_coverage(self, holistic_results, payload, retrieved_chunk_ids):
        """
        Increase KB chunk coverage by generating new samples for those KB chunks
        that were never retrieved during generation.

        Args:
            holistic_results (dict): Overall generated data including plans.
            payload (dict): The payload including a "knowledge_base" with table_name and doc_id.
            retrieved_chunk_ids (set): KB chunk IDs that have been retrieved during generation.

        Returns:
            dict: Updated holistic_results with additional samples for unused KB chunks.
        """
        import random

        # Make a copy of the original results to preserve the structure
        original_results = {
            "plans": {
                plan_id: plan_data.copy()
                for plan_id, plan_data in holistic_results["plans"].items()
            }
        }

        # Count the total number of datapoints in the original results
        original_datapoint_count = 0
        for plan_id, plan_data in original_results["plans"].items():
            original_datapoint_count += len(plan_data.get("generated_data", {}))

        table_name = payload["knowledge_base"]["table_name"]
        doc_ids = payload["knowledge_base"]["doc_ids"]

        # Instantiate KBSeedInstructionAgent to use its database client
        seed_agent = KBSeedInstructionAgent(table_name=table_name, doc_ids=doc_ids)

        # Get all KB chunks using get_random_examples with 100% fraction
        all_chunks = seed_agent.fetch_random_seeds(percentage=100)

        # Extract chunk IDs from the results
        all_chunk_ids = []
        chunk_id_to_data = {}

        for chunk in all_chunks:
            id_, eval_id, vector, metadata_key, metadata_value, _ = chunk
            # Create a metadata dictionary for this chunk
            chunk_data = {}
            chunk_id = None
            for key, value in zip(metadata_key, metadata_value):
                chunk_data[key] = value
                if key == "chunk_id":
                    chunk_id = value

            if chunk_id:
                all_chunk_ids.append(chunk_id)
                chunk_id_to_data[chunk_id] = chunk_data

        # Compute unused chunks
        unused_chunks = set(all_chunk_ids) - set(retrieved_chunk_ids)

        if not unused_chunks:
            return holistic_results

        # Prepare a dedicated plan for unused coverage
        plan_key = "kb_unused_coverage"
        if plan_key not in holistic_results["plans"]:
            holistic_results["plans"][plan_key] = {
                "plan_details": {"name": "KB Unused Coverage Plan"},
                "generated_data": {},
            }

        branch_name_lookup = holistic_results.get("plan_branch_names")
        if branch_name_lookup is not None:
            branch_name_lookup.setdefault(plan_key, plan_key)

        # Get the next ID for new samples
        existing_ids = []
        if holistic_results["plans"][plan_key]["generated_data"]:
            try:
                existing_ids = [
                    int(k)
                    for k in holistic_results["plans"][plan_key][
                        "generated_data"
                    ].keys()
                ]
            except Exception as e:
                logger.exception(f"Error converting existing IDs: {e}")
                existing_ids = [0]
        next_id = max(existing_ids) + 1 if existing_ids else 1

        # Process unused chunks in groups with aggregation (one sample per group)
        # Only process up to 20% of unused chunks
        max_chunks_to_process = int(len(unused_chunks) * 0.2)
        group_size = 5
        unused_chunks_list = list(unused_chunks)[:max_chunks_to_process]

        # Track newly generated datapoints
        new_datapoints_count = 0

        while unused_chunks_list:
            group = unused_chunks_list[:group_size]
            unused_chunks_list = unused_chunks_list[group_size:]

            # Gather datapoints for the group
            group_datapoints = []
            for chunk_id in group:
                if chunk_id in chunk_id_to_data:
                    group_datapoints.append(chunk_id_to_data[chunk_id])

            if not group_datapoints:
                continue
            datapoints_text = []
            for dp in group_datapoints:
                datapoints_text.append(dp.get("chunk_text", ""))
            # Create individual instructions for each chunk in the group
            instructions = {}
            for i, chunk_id in enumerate(group, 1):
                if chunk_id in chunk_id_to_data:
                    chunk_text = chunk_id_to_data[chunk_id].get(
                        "chunk_text", f"Unavailable text for chunk {chunk_id}"
                    )
                    instructions[str(i)] = {
                        "instruction": f"Generate synthetic data based on this KB chunk: {chunk_text}"
                    }

            # Create a plan for this group
            plan_details = {
                "Skill": "KB Unused Coverage",
                "Characteristic": "Unused",
                "name": f"unused_plan_{group[0]}",
                "GROUNDING INSTRUCTION": "STRICTLY use the provided domain knowledge from the knowledge base. Do NOT introduce information or assumptions outside the provided data points. If any required information is not explicitly in the provided datapoints, do NOT attempt to generate it. There should be **ZERO** hallucinations. (You are allowed to do domain expansion for generating a datapoint. For example: if we know from supplied domain knowledge that the context is about a restaurant, we will generate restaurant names in name column unless clearly specified). ",
                "Datapoints": datapoints_text,
            }

            # Fetch few-shot examples for each instruction
            few_shots_per_datapoint = seed_agent.fetch_few_shots_per_datapoint(
                instructions, plan_details, 4
            )

            # Enhance each instruction with its few-shot examples
            for i, chunk_id in enumerate(group, 1):
                i_str = str(i)
                if i_str in few_shots_per_datapoint and few_shots_per_datapoint[i_str]:
                    few_shots = few_shots_per_datapoint[i_str]
                    few_shots_text = "\n\nSimilar examples:\n" + "\n".join(
                        [str(fs) for fs in few_shots]
                    )
                    instructions[i_str]["instruction"] += few_shots_text

            # Aggregate the enhanced instructions into a single plan instruction for the group
            aggregated_instruction = (
                "Generate synthetic data for the following KB chunks:\n\n"
            )
            for inst_key in sorted(instructions.keys(), key=lambda x: int(x)):
                aggregated_instruction += (
                    f"Chunk {inst_key}: {instructions[inst_key]['instruction']}\n\n"
                )

            # Generate synthetic data for the aggregated group
            seed_val = random.randint(0, 1000)
            new_generated = self._generate_data_for_plan(
                aggregated_instruction, payload, dynamic_batch_size=1, seed=seed_val
            )

            if new_generated:
                # Assume the generation returns a single aggregated sample.
                sample = (
                    new_generated.get("1", new_generated)
                    if isinstance(new_generated, dict)
                    else new_generated
                )
                sample["chunk_ids"] = group
                sample["original_plan_id"] = plan_key
                holistic_results["plans"][plan_key]["generated_data"][str(next_id)] = (
                    sample
                )
                next_id += 1
                new_datapoints_count += 1
            else:
                logger.info(f"No sample generated for group: {group}")

            # Mark these chunks as now covered
            retrieved_chunk_ids.update(group)

        # Calculate the batch size from the payload
        batch_size = payload.get("batch_size", 10)

        # Calculate how many points we need to keep from original data
        points_to_keep = batch_size - new_datapoints_count

        # Collect all original datapoints
        all_original_datapoints = []
        for plan_id, plan_data in original_results["plans"].items():
            for data_id, data_point in plan_data.get("generated_data", {}).items():
                all_original_datapoints.append((plan_id, data_id))

        # Randomly select points to keep from original data
        if all_original_datapoints:
            to_keep = random.sample(
                all_original_datapoints,
                min(points_to_keep, len(all_original_datapoints)),
            )

            # Create new holistic_results with only kept points
            new_holistic_results: Dict[str, Any] = {"plans": {}}
            branch_name_lookup = holistic_results.get("plan_branch_names")

            # First add the kept original points
            for plan_id, data_id in to_keep:
                if plan_id not in new_holistic_results["plans"]:
                    new_holistic_results["plans"][plan_id] = {
                        "plan_details": original_results["plans"][plan_id][
                            "plan_details"
                        ],
                        "generated_data": {},
                    }
                new_holistic_results["plans"][plan_id]["generated_data"][data_id] = (
                    original_results["plans"][plan_id]["generated_data"][data_id]
                )

            # Then add all new points from kb_unused_coverage
            if "kb_unused_coverage" in holistic_results["plans"]:
                new_holistic_results["plans"]["kb_unused_coverage"] = holistic_results[
                    "plans"
                ]["kb_unused_coverage"]

            if branch_name_lookup:
                new_holistic_results["plan_branch_names"] = branch_name_lookup

            holistic_results = new_holistic_results

        return holistic_results

    def check_answers(self, answers):
        """
        convert answers json to a df and check if the df in completely nan
        """

        # Convert the answers JSON to a DataFrame
        try:
            if not answers:
                return False

            data_for_df = answers
            if isinstance(answers, dict):
                # If values are dicts, assume it's a collection of records {'1':{...}, '2':{...}}
                if answers and all(isinstance(v, dict) for v in answers.values()):
                    data_for_df = list(answers.values())
                else:  # Assume it's a single record that needs to be in a list
                    data_for_df = [answers]

            df = pd.DataFrame(data_for_df)
        except Exception as e:
            logger.error(
                f"Could not convert answers to DataFrame. Data: {answers}. Error: {e}"
            )
            return False

        # Check if the DataFrame is completely NaN
        is_all_null = df.isnull().all().all()
        logger.info(
            f"Check answers result: DataFrame shape is {df.shape}, is all null: {is_all_null}"
        )
        if is_all_null:
            return False
        else:
            return True

    def _detect_quality_issues(self, generated_batch, constraints=None):
        """Quick detection of common quality issues including custom property validation"""
        issues = []

        if not generated_batch or not isinstance(generated_batch, dict):
            return ["Invalid or empty batch structure"]

        # Create constraint lookup for efficient access
        constraint_lookup = {}
        if constraints:
            for constraint in constraints:
                if isinstance(constraint, dict) and "field" in constraint:
                    constraint_lookup[constraint["field"]] = constraint

        for record_id, record in generated_batch.items():
            if not isinstance(record, dict):
                issues.append(f"Record {record_id}: Invalid record structure")
                continue

            for field_name, field_value in record.items():
                # Get constraint for this field
                field_constraint = constraint_lookup.get(field_name, {})

                # Check for null/empty values
                if field_value is None or field_value == "" or field_value == "null":
                    issues.append(
                        f"Record {record_id}, Field {field_name}: Null/empty value"
                    )

                # Check for common placeholder patterns
                if isinstance(field_value, str):
                    placeholder_patterns = [
                        "example@",
                        "user123",
                        "lorem ipsum",
                        "placeholder",
                        "test@test",
                        "sample",
                        "dummy",
                        "n/a",
                        "tbd",
                        "todo",
                        "xxx",
                        "yyy",
                        "zzz",
                        "abc123",
                        "temp",
                    ]
                    field_lower = field_value.lower()
                    for pattern in placeholder_patterns:
                        if pattern in field_lower:
                            issues.append(
                                f"Record {record_id}, Field {field_name}: Placeholder text detected: {pattern}"
                            )

                # Check standard constraints
                if field_constraint:
                    # Type validation
                    expected_type = field_constraint.get("type", "").lower()
                    if expected_type == "string" and not isinstance(field_value, str):
                        issues.append(
                            f"Record {record_id}, Field {field_name}: Expected string, got {type(field_value).__name__}"
                        )
                    elif expected_type in ["int", "integer"] and not isinstance(
                        field_value, int
                    ):
                        issues.append(
                            f"Record {record_id}, Field {field_name}: Expected integer, got {type(field_value).__name__}"
                        )
                    elif expected_type == "float" and not isinstance(
                        field_value, (int, float)
                    ):
                        issues.append(
                            f"Record {record_id}, Field {field_name}: Expected float, got {type(field_value).__name__}"
                        )

                    # Length validation
                    if isinstance(field_value, str):
                        min_length = field_constraint.get("min_length", None)
                        max_length = field_constraint.get("max_length", None)
                        try:
                            if min_length and len(field_value) < int(re.sub(r"[^\d]", "", str(min_length)) or 0):
                                issues.append(
                                    f"Record {record_id}, Field {field_name}: Length {len(field_value)} below minimum {min_length}"
                                )
                            if max_length and len(field_value) > int(re.sub(r"[^\d]", "", str(max_length)) or 0):
                                issues.append(
                                    f"Record {record_id}, Field {field_name}: Length {len(field_value)} above maximum {max_length}"
                                )
                        except Exception as e:
                            logger.exception(
                                f"Error validating length for Record {record_id}, Field {field_name}: {str(e)}"
                            )
                            # Ignore length validation if min/max_length are not integers
                            pass

                    # Value restrictions (categorical)
                    allowed_values = field_constraint.get("values")
                    if (
                        allowed_values
                        and isinstance(allowed_values, list)
                        and field_value not in allowed_values
                    ):
                        issues.append(
                            f"Record {record_id}, Field {field_name}: Value '{field_value}' not in allowed values: {allowed_values}"
                        )

                    # Validate custom properties
                    custom_issues = self._validate_custom_properties(
                        field_value, field_constraint, field_name, record_id
                    )
                    issues.extend(custom_issues)

                # Check for overly short content in text fields (if no min_length specified)
                # if isinstance(field_value, str) and len(field_value.strip()) < 3 and not field_constraint.get('min_length'):
                #     issues.append(f"Record {record_id}, Field {field_name}: Content too short")

        return issues

    def _repair_data_issues(self, original_data, validation_issues, params):
        """Repair identified data quality issues"""
        repair_prompt = DATA_REPAIR_PROMPT.format(
            original_data=json.dumps(original_data, indent=2, ensure_ascii=False),
            validation_issues=json.dumps(
                validation_issues, indent=2, ensure_ascii=False
            ),
            ensure_ascii=False,
            schema=json.dumps(params["schema"], indent=2, ensure_ascii=False),
            requirements=json.dumps(
                params["requirements"], indent=2, ensure_ascii=False
            ),
        )

        try:
            if not self.should_continue():
                # optionally attach context/payload
                raise AbortFlow("Stopped because check failed")
            repair_response = self.llm._get_completion_content(
                messages=[{"role": "user", "content": repair_prompt}],
                response_format={"type": "json_object"},
            )

            # Clean and parse the response
            repair_response = repair_response.strip()
            if not repair_response.startswith("{"):
                start_idx = repair_response.find("{")
                if start_idx != -1:
                    repair_response = repair_response[start_idx:]
            if not repair_response.endswith("}"):
                end_idx = repair_response.rfind("}")
                if end_idx != -1:
                    repair_response = repair_response[: end_idx + 1]

            return json.loads(repair_response)
        except json.JSONDecodeError as e:
            return original_data  # Return original if repair fails

    async def _repair_data_issues_async(self, original_data, validation_issues, params):
        """Repair identified data quality issues asynchronously"""
        repair_prompt = DATA_REPAIR_PROMPT.format(
            original_data=json.dumps(original_data, indent=2, ensure_ascii=False),
            validation_issues=json.dumps(
                validation_issues, indent=2, ensure_ascii=False
            ),
            ensure_ascii=False,
            schema=json.dumps(params["schema"], indent=2, ensure_ascii=False),
            requirements=json.dumps(
                params["requirements"], indent=2, ensure_ascii=False
            ),
        )

        try:
            if not await self.should_continue_async():
                raise AbortFlow("Stopped because check failed")
            repair_response = await self.llm._get_completion_content_async(
                messages=[{"role": "user", "content": repair_prompt}],
                response_format={"type": "json_object"},
            )

            repair_response = repair_response.strip()
            if not repair_response.startswith("{"):
                start_idx = repair_response.find("{")
                if start_idx != -1:
                    repair_response = repair_response[start_idx:]
            if not repair_response.endswith("}"):
                end_idx = repair_response.rfind("}")
                if end_idx != -1:
                    repair_response = repair_response[: end_idx + 1]

            return json.loads(repair_response)
        except json.JSONDecodeError:
            return original_data  # Return original if repair fails

    def _validate_simulation_generated_data(self, plan_data, payload):
        """Validates the generated data against the original payload using PersonaConfigurator."""
        for plan_id, generated_data in plan_data["generated_data"].items():
            persona = generated_data.get("persona", {})
            try:
                # Extract constraints to pass as valid_values
                # Original logic: payload["constraints"][0]["property"]
                # We assume this structure exists if we are in this validation block
                constraints = payload.get("constraints", [])
                valid_values = None
                if (
                    constraints
                    and isinstance(constraints, list)
                    and len(constraints) > 0
                ):
                    prop = constraints[0].get("property")
                    if prop:
                        valid_values = prop

                corrected_data = PersonaConfigurator.validate_and_correct_persona(
                    persona, mode=self.simulation_mode, valid_values=valid_values
                )

                plan_data["generated_data"][plan_id]["persona"] = corrected_data
                # print("Corrected persona:", corrected_data)

            except Exception as e:
                logger.error(f"Error validating persona data for plan {plan_id}: {e}")
                traceback.print_exc()

        return plan_data

    def _validate_custom_properties(
        self, field_value, constraint, field_name, record_id
    ):
        """Validate field value against all custom properties defined in constraint"""
        issues = []

        # Get all constraint properties (both standard and custom)
        constraint_props = constraint.copy() if isinstance(constraint, dict) else {}

        # Standard properties that are handled elsewhere
        standard_props = {
            "field",
            "type",
            "content",
            "values",
            "min_length",
            "max_length",
        }

        # Check all custom properties
        for prop_name, prop_value in constraint_props.items():
            if prop_name in standard_props:
                continue  # Skip standard properties

            # Handle custom validation based on property name patterns
            try:
                if prop_name.startswith("min_") and prop_name != "min_length":
                    # Custom minimum constraints (e.g., min_words, min_sentences, min_value)
                    if (
                        isinstance(field_value, (int, float))
                        and field_value < prop_value
                    ):
                        issues.append(
                            f"Record {record_id}, Field {field_name}: Value {field_value} below minimum {prop_name}: {prop_value}"
                        )
                    elif isinstance(field_value, str):
                        if (
                            prop_name == "min_words"
                            and len(field_value.split()) < prop_value
                        ):
                            issues.append(
                                f"Record {record_id}, Field {field_name}: {len(field_value.split())} words below minimum {prop_value}"
                            )
                        elif (
                            prop_name == "min_sentences"
                            and len([s for s in field_value.split(".") if s.strip()])
                            < prop_value
                        ):
                            issues.append(
                                f"Record {record_id}, Field {field_name}: Sentences below minimum {prop_value}"
                            )

                elif prop_name.startswith("max_") and prop_name != "max_length":
                    # Custom maximum constraints (e.g., max_words, max_sentences, max_value)
                    if (
                        isinstance(field_value, (int, float))
                        and field_value > prop_value
                    ):
                        issues.append(
                            f"Record {record_id}, Field {field_name}: Value {field_value} above maximum {prop_name}: {prop_value}"
                        )
                    elif isinstance(field_value, str):
                        if (
                            prop_name == "max_words"
                            and len(field_value.split()) > prop_value
                        ):
                            issues.append(
                                f"Record {record_id}, Field {field_name}: {len(field_value.split())} words above maximum {prop_value}"
                            )
                        elif (
                            prop_name == "max_sentences"
                            and len([s for s in field_value.split(".") if s.strip()])
                            > prop_value
                        ):
                            issues.append(
                                f"Record {record_id}, Field {field_name}: Sentences above maximum {prop_value}"
                            )

                elif prop_name == "format":
                    # Custom format validation
                    if isinstance(field_value, str):
                        if prop_value == "email" and "@" not in field_value:
                            issues.append(
                                f"Record {record_id}, Field {field_name}: Invalid email format"
                            )
                        elif prop_value == "phone" and not re.match(
                            r"[\d\-\+\(\)\s]+", field_value
                        ):
                            issues.append(
                                f"Record {record_id}, Field {field_name}: Invalid phone format"
                            )
                        elif prop_value == "url" and not field_value.startswith(
                            ("http://", "https://", "www.")
                        ):
                            issues.append(
                                f"Record {record_id}, Field {field_name}: Invalid URL format"
                            )

                elif prop_name == "pattern":
                    # Regex pattern validation
                    if isinstance(field_value, str) and not re.match(
                        prop_value, field_value
                    ):
                        issues.append(
                            f"Record {record_id}, Field {field_name}: Does not match required pattern: {prop_value}"
                        )

                elif prop_name == "contains":
                    # Must contain specific text/values
                    if isinstance(field_value, str) and prop_value not in field_value:
                        issues.append(
                            f"Record {record_id}, Field {field_name}: Must contain '{prop_value}'"
                        )

                elif prop_name == "starts_with":
                    # Must start with specific text
                    if isinstance(field_value, str) and not field_value.startswith(
                        prop_value
                    ):
                        issues.append(
                            f"Record {record_id}, Field {field_name}: Must start with '{prop_value}'"
                        )

                elif prop_name == "ends_with":
                    # Must end with specific text
                    if isinstance(field_value, str) and not field_value.endswith(
                        prop_value
                    ):
                        issues.append(
                            f"Record {record_id}, Field {field_name}: Must end with '{prop_value}'"
                        )

                elif prop_name == "exclude":
                    # Must not contain specific values
                    exclude_list = (
                        prop_value if isinstance(prop_value, list) else [prop_value]
                    )
                    for exclude_val in exclude_list:
                        if isinstance(field_value, str) and exclude_val in field_value:
                            issues.append(
                                f"Record {record_id}, Field {field_name}: Must not contain '{exclude_val}'"
                            )

                elif prop_name == "case":
                    # Case requirements (upper, lower, title, etc.)
                    if isinstance(field_value, str):
                        if prop_value == "upper" and field_value != field_value.upper():
                            issues.append(
                                f"Record {record_id}, Field {field_name}: Must be uppercase"
                            )
                        elif (
                            prop_value == "lower" and field_value != field_value.lower()
                        ):
                            issues.append(
                                f"Record {record_id}, Field {field_name}: Must be lowercase"
                            )
                        elif (
                            prop_value == "title" and field_value != field_value.title()
                        ):
                            issues.append(
                                f"Record {record_id}, Field {field_name}: Must be title case"
                            )

                elif prop_name == "unique":
                    # Uniqueness constraint (would need to be checked across all records)
                    pass  # This requires cross-record validation

                elif prop_name == "required":
                    # Required field validation
                    if prop_value and (field_value is None or field_value == ""):
                        issues.append(
                            f"Record {record_id}, Field {field_name}: Required field is empty"
                        )

                elif prop_name in ["language", "locale"]:
                    # Language/locale specific validation
                    pass  # Could implement language detection if needed

                elif prop_name == "sentiment":
                    # Sentiment requirements (positive, negative, neutral)
                    pass  # Could implement sentiment analysis if needed

                elif prop_name == "tone":
                    # Tone requirements (formal, casual, professional, etc.)
                    pass  # Could implement tone analysis if needed

                # Add more custom property validations as needed

            except Exception as e:
                issues.append(
                    f"Record {record_id}, Field {field_name}: Error validating custom property {prop_name}: {str(e)}"
                )

        return issues

    def _validate_and_repair_batch(
        self, generated_batch, params, max_repair_attempts=2
    ):
        """Validate a batch of generated data and repair issues if found"""
        if not generated_batch:
            return generated_batch

        # Quick quality check first
        try:
            quick_issues = self._detect_quality_issues(
                generated_batch, params.get("constraints", [])
            )
        except Exception as e:
            logger.exception(f"Error during quick quality check: {str(e)}")
            quick_issues = None
        # if quick_issues:
        # logger.warning(f"⚠ Quick quality check found {len(quick_issues)} issues")
        # for issue in quick_issues[:5]:  # Show first 5 issues
        #     logger.warning(f"  - {issue}")

        # If no major issues, skip expensive LLM validation for small batches
        if len(quick_issues) == 0 and len(generated_batch) <= 5:
            # logger.info(
            #     "✓ Quick validation passed, skipping detailed validation for small batch"
            # )
            return generated_batch

        # Full LLM-based validation for larger batches or when issues detected
        validation_result = self._validate_data_quality(generated_batch, params)

        # If validation passes, return the data
        if validation_result.get("validation_status") == "PASS":
            # logger.info("✓ Data quality validation passed")
            return generated_batch

        # logger.info(
        #     f"⚠ Data quality issues detected. Overall score: {validation_result.get('overall_quality_score', 0.0)}"
        # )

        # Attempt repairs
        repaired_data = generated_batch.copy()
        repaired_data_org = generated_batch.copy()
        for attempt in range(max_repair_attempts):
            # logger.info(f"🔧 Attempting repair {attempt + 1}/{max_repair_attempts}")

            # Repair the data
            repaired_data = self._repair_data_issues(
                repaired_data, validation_result.get("specific_issues", []), params
            )
            # Re-validate the repaired data
            validation_result = self._validate_data_quality(repaired_data, params)

            if validation_result.get("validation_status") == "PASS":
                # logger.info(
                #     f"✓ Data successfully repaired after {attempt + 1} attempts"
                # )
                return repaired_data
            elif validation_result.get("overall_quality_score", 0.0) > 0.8:
                # logger.info(
                #     f"✓ Data quality significantly improved (score: {validation_result.get('overall_quality_score', 0.0)})"
                # )
                return repaired_data

        logger.info("Maximum repair attempts reached. Returning best available data.")
        return repaired_data_org

    async def _validate_and_repair_batch_async(
        self, generated_batch, params, max_repair_attempts=2
    ):
        """Validate a batch of generated data and repair issues if found asynchronously"""
        if not generated_batch:
            return generated_batch

        try:
            quick_issues = self._detect_quality_issues(
                generated_batch, params.get("constraints", [])
            )
        except Exception as e:
            logger.exception(f"Error during quick quality check: {str(e)}")
            quick_issues = []
        # if quick_issues:
        #     logger.warning(f"⚠ Quick quality check found {len(quick_issues)} issues")
        #     for issue in quick_issues[:5]:
        #         # logger.warning(f"  - {issue}")

        if len(quick_issues) == 0 and len(generated_batch) <= 5:
            # logger.info(
            #     "✓ Quick validation passed, skipping detailed validation for small batch"
            # )
            return generated_batch

        validation_result = await self._validate_data_quality_async(
            generated_batch, params
        )

        if validation_result.get("validation_status") == "PASS":
            # logger.info("✓ Data quality validation passed")
            return generated_batch

        # logger.info(
        #     f"⚠ Data quality issues detected. Overall score: {validation_result.get('overall_quality_score', 0.0)}"
        # )

        repaired_data = generated_batch.copy()
        repaired_data_org = generated_batch.copy()
        for attempt in range(max_repair_attempts):
            # logger.info(f"🔧 Attempting repair {attempt + 1}/{max_repair_attempts}")

            repaired_data = await self._repair_data_issues_async(
                repaired_data, validation_result.get("specific_issues", []), params
            )
            validation_result = await self._validate_data_quality_async(
                repaired_data, params
            )

            if validation_result.get("validation_status") == "PASS":
                # logger.info(
                #     f"✓ Data successfully repaired after {attempt + 1} attempts"
                # )
                return repaired_data
            elif validation_result.get("overall_quality_score", 0.0) > 0.8:
                # logger.info(
                #     f"✓ Data quality significantly improved (score: {validation_result.get('overall_quality_score', 0.0)})"
                # )
                return repaired_data

        logger.info("Maximum repair attempts reached. Returning best available data.")
        return repaired_data_org

    def _validate_all_data(self, all_generated_data, params):
        """Validate the complete generated dataset"""
        validation_prompt = VALIDATION_PROMPT.format(
            **params,
            generated_data=json.dumps(all_generated_data, indent=2, ensure_ascii=False),
            quality_thresholds=json.dumps(
                self.quality_thresholds, indent=2, ensure_ascii=False
            ),
        )

        try:
            if not self.should_continue():
                # optionally attach context/payload
                raise AbortFlow("Stopped because check failed")
            validation_response = self.llm._get_completion_content(
                messages=[{"role": "user", "content": validation_prompt}],
                response_format={"type": "json_object"},
            )
            return json.loads(validation_response)
        except json.JSONDecodeError:
            return self._fix_json_response(validation_response)

    def _validate_data_quality(self, generated_data, params):
        """Validate data quality with focus on human-like characteristics and completeness"""
        validation_prompt = DATA_QUALITY_VALIDATION_PROMPT.format(
            generated_data=json.dumps(generated_data, indent=2, ensure_ascii=False),
            schema=json.dumps(params["schema"], indent=2, ensure_ascii=False),
            requirements=json.dumps(
                params["requirements"], indent=2, ensure_ascii=False
            ),
            constraints=json.dumps(params["constraints"], indent=2, ensure_ascii=False),
            ensure_ascii=False,
        )

        try:
            if not self.should_continue():
                # optionally attach context/payload
                raise AbortFlow("Stopped because check failed")
            validation_response = self.llm._get_completion_content(
                messages=[{"role": "user", "content": validation_prompt}],
                response_format={"type": "json_object"},
            )
            return json.loads(validation_response)
        except json.JSONDecodeError as e:
            # Return a default failure response
            return {
                "validation_status": "FAIL",
                "overall_quality_score": 0.0,
                "validation_results": {
                    "completeness": {
                        "status": "FAIL",
                        "null_empty_count": 999,
                        "placeholder_count": 999,
                        "failed_records": [],
                    },
                    "authenticity": {
                        "status": "FAIL",
                        "human_like_score": 0.0,
                        "artificial_patterns_detected": 999,
                        "failed_records": [],
                    },
                    "contextual_coherence": {
                        "status": "FAIL",
                        "coherence_score": 0.0,
                        "inconsistent_records": [],
                    },
                    "diversity": {
                        "status": "FAIL",
                        "diversity_score": 0.0,
                        "repetitive_patterns": 999,
                    },
                },
                "specific_issues": [],
                "recommendations": ["Validation failed due to JSON parsing error"],
            }

    async def _validate_data_quality_async(self, generated_data, params):
        """Validate data quality with focus on human-like characteristics and completeness asynchronously"""
        validation_prompt = DATA_QUALITY_VALIDATION_PROMPT.format(
            generated_data=json.dumps(generated_data, indent=2, ensure_ascii=False),
            schema=json.dumps(params["schema"], indent=2, ensure_ascii=False),
            requirements=json.dumps(
                params["requirements"], indent=2, ensure_ascii=False
            ),
            constraints=json.dumps(params["constraints"], indent=2, ensure_ascii=False),
            ensure_ascii=False,
        )

        try:
            if not await self.should_continue_async():
                raise AbortFlow("Stopped because check failed")
            validation_response = await self.llm._get_completion_content_async(
                messages=[{"role": "user", "content": validation_prompt}],
                response_format={"type": "json_object"},
            )
            return json.loads(validation_response)
        except json.JSONDecodeError:
            return {
                "validation_status": "FAIL",
                "overall_quality_score": 0.0,
                "validation_results": {
                    "completeness": {
                        "status": "FAIL",
                        "null_empty_count": 999,
                        "placeholder_count": 999,
                        "failed_records": [],
                    },
                    "authenticity": {
                        "status": "FAIL",
                        "human_like_score": 0.0,
                        "artificial_patterns_detected": 999,
                        "failed_records": [],
                    },
                    "contextual_coherence": {
                        "status": "FAIL",
                        "coherence_score": 0.0,
                        "inconsistent_records": [],
                    },
                    "diversity": {
                        "status": "FAIL",
                        "diversity_score": 0.0,
                        "repetitive_patterns": 999,
                    },
                },
                "specific_issues": [],
                "recommendations": ["Validation failed due to JSON parsing error"],
            }

    def _fix_json_response(self, invalid_response):
        """Attempts to fix invalid JSON response using a repair agent"""
        # logger.warning(f"Attempting to fix invalid JSON response")
        repair_prompt = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"""You are a JSON repair agent. Fix the following invalid JSON response to match the required validation format:

Original Response:
{invalid_response}

Required JSON Format:
{{
    "validation_result": {{
        "status": "ACCEPTED|REJECTED",
        "quality_score": <float>,
        "statistical_validity": {{...}},
        "quality_metrics": {{...}},
        "failing_criteria": [...],
        "improvement_recommendations": [...],
        "explanation": "<explanation text>"
    }}
}}

Return ONLY the fixed JSON, nothing else. do not start with ```json and end with ``` just return the json structure""",
                }
            ],
        }

        try:
            if not self.should_continue():
                # optionally attach context/payload
                raise AbortFlow("Stopped because check failed")
            fixed_response = self.llm._get_completion_content(
                messages=[repair_prompt], response_format={"type": "json_object"}
            )
            # logger.info(f"Successfully fixed JSON response")
            return json.loads(fixed_response)
        except json.JSONDecodeError:
            # logger.error("Failed to fix JSON response after repair attempt.")
            return {
                "validation_result": {
                    "status": "REJECTED",
                    "quality_score": 0.0,
                    "statistical_validity": {},
                    "quality_metrics": {},
                    "failing_criteria": [
                        {"criterion": "json_parsing", "impact": "HIGH"}
                    ],
                    "improvement_recommendations": [
                        {
                            "issue": "Failed to parse validation response",
                            "suggested_action": "Review generation parameters",
                            "priority": "HIGH",
                        }
                    ],
                    "explanation": "Failed to parse validation response. Rejecting batch as precaution.",
                }
            }

    async def _fix_json_response_async(self, invalid_response):
        """Attempts to fix invalid JSON response using a repair agent asynchronously"""
        # logger.warning(f"Attempting to fix invalid JSON response")
        repair_prompt = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"""You are a JSON repair agent. Fix the following invalid JSON response to match the required validation format:

Original Response:
{invalid_response}

Required JSON Format:
{{
    "validation_result": {{
        "status": "ACCEPTED|REJECTED",
        "quality_score": <float>,
        "statistical_validity": {{...}},
        "quality_metrics": {{...}},
        "failing_criteria": [...],
        "improvement_recommendations": [...],
        "explanation": "<explanation text>"
    }}
}}

Return ONLY the fixed JSON, nothing else. do not start with ```json and end with ``` just return the json structure""",
                }
            ],
        }

        try:
            if not await self.should_continue_async():
                raise AbortFlow("Stopped because check failed")
            fixed_response = await self.llm._get_completion_content_async(
                messages=[repair_prompt], response_format={"type": "json_object"}
            )
            # logger.info(f"Successfully fixed JSON response")
            return json.loads(fixed_response)
        except json.JSONDecodeError:
            # logger.error("Failed to fix JSON response after repair attempt.")
            return {
                "validation_result": {
                    "status": "REJECTED",
                    "quality_score": 0.0,
                    "statistical_validity": {},
                    "quality_metrics": {},
                    "failing_criteria": [
                        {"criterion": "json_parsing", "impact": "HIGH"}
                    ],
                    "improvement_recommendations": [
                        {
                            "issue": "Failed to parse validation response",
                            "suggested_action": "Review generation parameters",
                            "priority": "HIGH",
                        }
                    ],
                    "explanation": "Failed to parse validation response. Rejecting batch as precaution.",
                }
            }

    def _fix_json_response_plans(self, invalid_response):
        """Attempts to fix invalid JSON response using a repair agent"""
        # logger.warning(f"Attempting to fix invalid JSON response for plans")
        repair_prompt = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"""You are a JSON repair agent. Fix the following invalid JSON response to match the required format:

Original Response:
{invalid_response}

Return the strategies in this JSON format only:
{{
    "1": {{
        "name": "Strategy name",
        "column_focus": {{
            "column_name": {{
                "type": "categorical|numerical|datetime|text|boolean",
                "value_range": "Specific values or ranges this strategy will use",
                "distribution": "How values will be distributed within the range"
            }}
        }},
        "generation_rules": [
            "Rules for generating values for each focused column"
        ],
        "validation_criteria": [
            "Rules for validating the generated values"
        ]
    }},
    ... // Repeat for strategies ...
}}
Rules: 
1. Return only valid JSON matching the required format. 
2. Remove any extra text in the beginning or end. 
3. Do not start with ```json and end with ``` just return the json structure""",
                }
            ],
        }

        try:
            # Get repaired response
            if not self.should_continue():
                # optionally attach context/payload
                raise AbortFlow("Stopped because check failed")
            fixed_response = self.llm._get_completion_content(
                messages=[repair_prompt], response_format={"type": "json_object"}
            )
            # logger.info(f"Successfully fixed JSON response for plans")
            return json.loads(fixed_response)
        except json.JSONDecodeError:
            # logger.error("Failed to fix JSON response for plans after repair attempt.")
            # If repair fails, return an error message in json format
            return {
                "error": {
                    "message": "Unable to generate data at this time, please try again later.",
                }
            }

    def _fix_json_response_ins(self, invalid_response):
        """Attempts to fix invalid JSON response using a repair agent"""

        repair_prompt = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"""You are a JSON repair agent. Fix the following invalid JSON response to match the required format:

Original Response:
{invalid_response}

Return the strategies in this JSON format only:
{{
   "1":{{"instruction for data point 1": "Generate a data point where ..."}},
   "2":{{"instruction for data point 2": "Generate a data point where ..."}},
   "3":{{"instruction for data point 3": "Generate a data point where ..."}},
      ...
   "10":{{"instruction for data point 10": "Generate a data point where ..."}},
}}
Rules:
1. Return valid JSON matching the required format. Remove any extra text in the begining or end.
2. Response should only use the curly braces {{ and }} and not the square brackets [ and ]. If you see any square brackets, remove them and replace them with curly braces respectively.
3. Return ONLY the fixed JSON, nothing else.
4. do not start with ```json and end with ``` just return the json structure""",
                }
            ],
        }

        try:
            # Get repaired response
            if not self.should_continue():
                # optionally attach context/payload
                raise AbortFlow("Stopped because check failed")
            fixed_response = self.llm._get_completion_content(
                messages=[repair_prompt], response_format={"type": "json_object"}
            )
            return json.loads(fixed_response)
        except json.JSONDecodeError:
            # If repair fails, return a safe fallback response
            raise ValueError("Repair Failed")

    async def _fix_json_response_ins_async(self, invalid_response):
        """Attempts to fix invalid JSON response using a repair agent asynchronously"""

        repair_prompt = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"""You are a JSON repair agent. Fix the following invalid JSON response to match the required format:

Original Response:
{invalid_response}

Return the strategies in this JSON format only:
{{
   "1":{{"instruction for data point 1": "Generate a data point where ..."}},
   "2":{{"instruction for data point 2": "Generate a data point where ..."}},
   "3":{{"instruction for data point 3": "Generate a data point where ..."}},
      ...
   "10":{{"instruction for data point 10": "Generate a data point where ..."}},
}}
Rules:
1. Return valid JSON matching the required format. Remove any extra text in the begining or end.
2. Response should only use the curly braces {{ and }} and not the square brackets [ and ]. If you see any square brackets, remove them and replace them with curly braces respectively.
3. Return ONLY the fixed JSON, nothing else.
4. do not start with ```json and end with ``` just return the json structure""",
                }
            ],
        }

        try:
            if not await self.should_continue_async():
                raise AbortFlow("Stopped because check failed")
            fixed_response = await self.llm._get_completion_content_async(
                messages=[repair_prompt], response_format={"type": "json_object"}
            )
            return json.loads(fixed_response)
        except json.JSONDecodeError:
            raise ValueError("Repair Failed")

    def _fix_json_response_generations(self, invalid_response):
        """Attempts to fix invalid JSON response using a repair agent"""

        repair_prompt = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"""You are a JSON repair agent. Fix the following invalid JSON response to match the required format:

Original Response:
{invalid_response}

Return the strategies in this JSON format only:
{{
  "1": {{
    "field_1": <value>,
    "field_2": <value>,
    ...
  }},
  "2": {{
    "field_1": <value>,
    "field_2": <value>,
    ...
  }},
  ...
}}
Rules:
1. Return valid JSON matching the required format. 
2. Remove any extra text, explanations, comment, in the begining or end.
3. Response should only use the curly braces {{ and }} and not the square brackets [ and ]. If you see any square brackets, remove them and replace them with curly braces respectively.
4. Return ONLY the fixed JSON, nothing else.
5. do not start with ```json and end with ``` just return the json structure""",
                }
            ],
        }

        try:
            # Get repaired response
            if not self.should_continue():
                # optionally attach context/payload
                raise AbortFlow("Stopped because check failed")
            fixed_response = self.llm._get_completion_content(
                messages=[repair_prompt], response_format={"type": "json_object"}
            )
            return json.loads(fixed_response)
        except json.JSONDecodeError:
            # If repair fails, return a safe fallback response
            return {
                "error": {
                    "message": "Unable to generate data at this time, please try again later.",
                }
            }

    async def _fix_json_response_generations_async(self, invalid_response):
        """Attempts to fix invalid JSON response using a repair agent asynchronously"""

        repair_prompt = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"""You are a JSON repair agent. Fix the following invalid JSON response to match the required format:

Original Response:
{invalid_response}

Return the strategies in this JSON format only:
{{
  "1": {{
    "field_1": <value>,
    "field_2": <value>,
    ...
  }},
  "2": {{
    "field_1": <value>,
    "field_2": <value>,
    ...
  }},
  ...
}}
Rules:
1. Return valid JSON matching the required format. 
2. Remove any extra text, explanations, comment, in the begining or end.
3. Response should only use the curly braces {{ and }} and not the square brackets [ and ]. If you see any square brackets, remove them and replace them with curly braces respectively.
4. Return ONLY the fixed JSON, nothing else.
5. do not start with ```json and end with ``` just return the json structure""",
                }
            ],
        }

        try:
            if not await self.should_continue_async():
                raise AbortFlow("Stopped because check failed")
            fixed_response = await self.llm._get_completion_content_async(
                messages=[repair_prompt], response_format={"type": "json_object"}
            )
            return json.loads(fixed_response)
        except json.JSONDecodeError:
            return {
                "error": {
                    "message": "Unable to generate data at this time, please try again later.",
                }
            }

    # def _delete_rows_from_dataset(
    #     self, dataset_id, row_ids, headers, base_url="http://localhost:80"
    # ):
    #     """
    #     [DEPRECATED] Delete rows from a dataset using the Model Hub API.
    #     This function is unused and will be removed in future versions.

    #     Args:
    #         dataset_id (str): ID of the dataset to delete rows from
    #         row_ids (list): List of row IDs to delete
    #         headers (dict): Request headers
    #         base_url (str): Base URL for the API endpoint
    #     """
    #     logger.warning(
    #         "DEPRECATED: _delete_rows_from_dataset is deprecated and unused."
    #     )
    #     try:
    #         response = requests.delete(
    #             f"{base_url}/model-hub/develops/{dataset_id}/delete_row/",
    #             json={"row_ids": row_ids},
    #             headers=headers,
    #         )
    #         if response.status_code != 200:
    #             logger.warning(f"Warning: Failed to delete rows: {response.text}")

    #     except Exception as e:
    #         logger.exception(f"Error deleting rows from dataset: {str(e)}")

    # def _update_rows_in_dataset(
    #     self, dataset_id, rows, headers, base_url="http://localhost:80"
    # ):
    #     """
    #     [DEPRECATED] Update rows in a dataset using the Model Hub API.
    #     This function is unused and will be removed in future versions.

    #     Args:
    #         dataset_id (str): ID of the dataset to update rows in
    #         rows (list): List of rows to update in the dataset.
    #         headers (dict): Request headers
    #         base_url (str): Base URL for the API endpoint
    #     """
    #     logger.warning("DEPRECATED: _update_rows_in_dataset is deprecated and unused.")
    #     try:
    #         headers["Content-Type"] = "application/json"
    #         response = requests.put(
    #             f"{base_url}/model-hub/develops/{dataset_id}/update_row/",
    #             json={"rows": rows},
    #             headers=headers,
    #         )
    #         if response.status_code != 200:
    #             logger.warning(f"Warning: Failed to update rows: {response.text}")
    #     except Exception as e:
    #         logger.exception(f"Error updating rows in dataset: {str(e)}")

    # def _add_rows_to_dataset(
    #     self, rows, dataset_id, headers, base_url="http://localhost:80"
    # ):
    #     """
    #     [DEPRECATED] Add rows to a dataset using the Model Hub API.
    #     This function is unused and will be removed in future versions.

    #     Args:
    #         rows (list): List of rows to add to the dataset.
    #         dataset_id (str): ID of the dataset to add rows to.
    #     """
    #     logger.warning("DEPRECATED: _add_rows_to_dataset is deprecated and unused.")
    #     try:
    #         headers["Content-Type"] = "application/json"
    #         for i in range(0, len(rows), 10):
    #             batch = rows[i : i + 10]
    #             response = requests.post(
    #                 f"{base_url}/model-hub/develops/{dataset_id}/add_rows/",
    #                 json={"rows": batch},
    #                 headers=headers,
    #             )

    #             if response.status_code != 200:
    #                 logger.warning(
    #                     f"Warning: Failed to send batch {i // 10 + 1} to API: {response.text}"
    #                 )

    #     except Exception as e:
    #         logger.exception(f"Error sending data to API: {str(e)}")
