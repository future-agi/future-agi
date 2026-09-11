"""Case generation — orchestrate SDA to produce raw cases for one intent.

Replaces ESA methods: generate_cases_for_single_intent, _generate_raw_cases_from_sda.

Key difference from ESA: does NOT do inline categorization or persona validation.
In v3, categorization runs inside the same activity after case generation,
and persona validation is a separate downstream activity.

Parallelism: Uses ThreadPoolExecutor for multi-branch SDA calls (no asyncio).
"""

import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import pandas as pd
import structlog

from django.db import close_old_connections

from ee.agenthub.scenario_graph.services.branch_metadata import (
    create_single_branch_metadata_string,
)
from ee.agenthub.scenario_graph.services.case_transformer import (
    convert_sda_data_to_cases,
)
from ee.agenthub.scenario_graph.services.payload_builder import (
    build_sda_payload,
)

logger = structlog.get_logger(__name__)

# Max parallel workers for SDA calls
MAX_SDA_WORKERS = 15


def generate_cases_for_intent(
    intent_id: str,
    intent_value: str,
    branches_metadata: List[Dict[str, Any]],
    batch_size: int,
    agent_context: Dict[str, Any],
    mode: str = "voice",
    custom_instruction: Optional[str] = None,
    configuration_snapshot: Optional[Dict[str, Any]] = None,
    custom_columns: Optional[List[Dict[str, Any]]] = None,
    property_list: Optional[List[Dict[str, Any]]] = None,
    graph_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Generate raw cases for a single intent via SDA.

    This replaces ESA.generate_cases_for_single_intent. Unlike ESA, it does NOT
    perform inline categorization or persona validation — those are handled
    downstream in the v3 pipeline.

    Args:
        intent_id: Unique identifier for this intent.
        intent_value: The intent description/value.
        branches_metadata: List of branch metadata from process_branches.
        batch_size: Number of cases to generate for this intent.
        agent_context: Flat dict with agent_name, description, languages, inbound, etc.
        mode: "voice" or "chat".
        custom_instruction: Optional user instruction.
        configuration_snapshot: Optional agent version config (unused currently).
        custom_columns: Optional custom column definitions.
        property_list: Optional persona property constraints.
        graph_id: Optional graph ID for context.

    Returns:
        List of raw case dicts with intent_id added.
    """
    logger.info(f"Generating {batch_size} cases for intent: {intent_value}")

    # Override custom instruction with intent context — matches ESA behavior
    # where self.custom_instruction is replaced at generation time.
    full_instruction = f"The user intent is: {intent_value}."

    if not branches_metadata:
        logger.warning("No branches metadata provided")
        return []

    # Build branch metadata payloads for SDA
    branch_metadata_payloads = []
    for md in branches_metadata:
        if "metadata_payload" in md:
            branch_metadata_payloads.append(md["metadata_payload"])
        else:
            payload = {
                "branch_name": md.get("branch_name", "unknown"),
                "metadata_string": create_single_branch_metadata_string(md),
            }
            branch_metadata_payloads.append(payload)

    # Use first branch as template
    template_branch = {
        "detailedPath": branches_metadata[0].get("detailedPath", []),
        "path": branches_metadata[0].get("path", []),
        "start_node": branches_metadata[0].get("start_node", ""),
        "end_node": branches_metadata[0].get("end_node", ""),
    }

    # Pre-check usage before the LLM call
    org_id = agent_context.get("organization_id")
    if org_id:
        try:
            from ee.usage.models.usage import APICallTypeChoices
        except ImportError:
            APICallTypeChoices = None
        try:
            from ee.usage.services.metering import check_usage
        except ImportError:
            check_usage = None

        usage_check = check_usage(org_id, APICallTypeChoices.SYNTHETIC_DATA_GENERATION.value)
        if not usage_check.allowed:
            raise ValueError(usage_check.reason or "Usage limit exceeded")

    # Generate raw data via SDA
    generated_data, actual_cost = _generate_raw_data_from_sda(
        template_branch=template_branch,
        branch_metadata_payloads=branch_metadata_payloads,
        rows=batch_size,
        agent_context=agent_context,
        mode=mode,
        custom_instruction=full_instruction,
        custom_columns=custom_columns,
        property_list=property_list,
    )

    if generated_data is None:
        return []

    # Log cost
    _log_generation_cost(generated_data, agent_context, actual_cost=actual_cost)

    # Convert SDA output to case dicts
    raw_cases = convert_sda_data_to_cases(
        generated_data,
        no_of_rows=batch_size,
        custom_columns=custom_columns,
    )

    # Add intent_id to each case
    for case in raw_cases:
        case["intent_id"] = intent_id

    logger.info(f"Generated {len(raw_cases)} cases for intent: {intent_value}")
    return raw_cases


def _generate_raw_data_from_sda(
    template_branch: Dict[str, Any],
    branch_metadata_payloads: List[Dict[str, Any]],
    rows: int,
    agent_context: Dict[str, Any],
    mode: str,
    custom_instruction: Optional[str],
    custom_columns: Optional[List[Dict[str, Any]]],
    property_list: Optional[List[Dict[str, Any]]],
):
    """Generate raw data from SDA, distributing across branches.

    For single branch: calls SDA.generate_and_validate directly.
    For multiple branches: ThreadPoolExecutor with sync SDA calls.

    Returns DataFrame or None on error.
    """
    from ee.agenthub.synthetic_data_agent.synthetic_data_agent import (
        SyntheticDataAgent,
    )

    if not template_branch.get("detailedPath"):
        logger.warning("No detailed path found in template branch")
        return None, 0.0

    num_branches = len(branch_metadata_payloads) if branch_metadata_payloads else 1

    # Distribute rows across branches
    batch_sizes = [rows // num_branches] * num_branches
    for i in range(rows % num_branches):
        batch_sizes[i] += 1

    logger.info(
        f"Generating with rows={rows}, branches={num_branches} "
        f"to ensure branch diversity"
    )

    # Build SDA payload
    sda_payload = build_sda_payload(
        agent_context=agent_context,
        detailed_branch=template_branch,
        rows=rows,
        mode=mode,
        custom_instruction=custom_instruction,
        custom_columns=custom_columns,
        property_list=property_list,
    )

    property_list_updated = sda_payload.get("property_list", [])

    if num_branches == 1:
        sda = SyntheticDataAgent(simulation_mode=mode)
        generated_data = sda.generate_and_validate(
            sda_payload,
            branch_metadatas=branch_metadata_payloads,
            called_for="simulate",
        )
        total_cost = getattr(getattr(sda, "llm", None), "cost", {}).get("total_cost", 0) or 0
    else:
        generated_data, total_cost = _run_parallel_sda_workers(
            sda_payload=sda_payload,
            branch_metadata_payloads=branch_metadata_payloads,
            batch_sizes=batch_sizes,
            property_list_updated=property_list_updated,
            mode=mode,
        )

    return generated_data, total_cost


def _sda_worker_sync(
    payload_template: Dict[str, Any],
    branch_meta: Dict[str, Any],
    batch_size: int,
    prop_choice: Optional[Dict],
    worker_mode: str,
):
    """Sync SDA worker for a single branch. Runs in a thread."""
    from ee.agenthub.synthetic_data_agent.synthetic_data_agent import (
        SyntheticDataAgent,
    )

    try:
        close_old_connections()
        local_sda = SyntheticDataAgent(simulation_mode=worker_mode)
        local_payload = copy.deepcopy(payload_template)
        local_payload["batch_size"] = int(batch_size)
        if prop_choice:
            local_payload["property_list"] = [prop_choice]

        data = local_sda.generate_and_validate(
            local_payload,
            branch_metadatas=[branch_meta],
            called_for="simulate",
        )
        cost = getattr(getattr(local_sda, "llm", None), "cost", {}).get("total_cost", 0) or 0
        return data, cost
    except Exception as e:
        logger.exception(
            f"Error in SDA worker for branch {branch_meta.get('branch_name')}: {e}"
        )
        return None, 0
    finally:
        close_old_connections()


def _run_parallel_sda_workers(
    sda_payload: Dict[str, Any],
    branch_metadata_payloads: List[Dict[str, Any]],
    batch_sizes: List[int],
    property_list_updated: list,
    mode: str,
):
    """Run parallel SDA workers for multi-branch generation.

    Uses ThreadPoolExecutor — fully sync, no asyncio.
    Falls back to single SDA call on parallel failure.
    """
    from ee.agenthub.synthetic_data_agent.synthetic_data_agent import (
        SyntheticDataAgent,
    )

    num_branches = len(branch_metadata_payloads)
    num_workers = min(num_branches, MAX_SDA_WORKERS)

    try:
        results = []
        total_cost = 0.0
        with ThreadPoolExecutor(max_workers=num_workers) as pool:
            futures = {}
            for i, branch_meta in enumerate(branch_metadata_payloads):
                bs = batch_sizes[i] if i < len(batch_sizes) else batch_sizes[0]
                if bs <= 0:
                    continue
                prop_choice = None
                if property_list_updated:
                    prop_choice = property_list_updated[i % len(property_list_updated)]
                future = pool.submit(
                    _sda_worker_sync,
                    sda_payload,
                    branch_meta,
                    bs,
                    prop_choice,
                    mode,
                )
                futures[future] = branch_meta.get("branch_name", f"branch_{i}")

            for future in as_completed(futures):
                branch_name = futures[future]
                try:
                    result, cost = future.result()
                    total_cost += cost or 0
                    if result is not None:
                        results.append(result)
                        logger.info(f"SDA worker completed for branch: {branch_name}")
                except Exception as e:
                    logger.exception(f"SDA worker failed for branch {branch_name}: {e}")

        if not results:
            return None, 0.0

        # Concat DataFrame results
        dfs = []
        for r in results:
            if hasattr(r, "columns") and hasattr(r, "to_dict"):
                dfs.append(r)
        if dfs:
            try:
                return pd.concat(dfs, ignore_index=True), total_cost
            except Exception:
                return results[0], total_cost
        else:
            return results[0], total_cost

    except Exception as e:
        logger.exception(f"Error in parallel SDA execution: {e}")
        # Fallback to single SDA call
        sda = SyntheticDataAgent(simulation_mode=mode)
        data = sda.generate_and_validate(
            sda_payload,
            branch_metadatas=branch_metadata_payloads,
            called_for="simulate",
        )
        cost = getattr(getattr(sda, "llm", None), "cost", {}).get("total_cost", 0) or 0
        return data, cost


def _log_generation_cost(generated_data, agent_context: Dict[str, Any], actual_cost: float = 0.0):
    """Log token usage and deduct cost for the generation."""
    try:
        try:
            from ee.usage.models.usage import APICallTypeChoices
        except ImportError:
            APICallTypeChoices = None
        try:
            from ee.usage.utils.usage_entries import count_text_tokens, log_and_deduct_cost_for_api_request
        except ImportError:
            count_text_tokens = None
            log_and_deduct_cost_for_api_request = None

        if not hasattr(generated_data, "columns"):
            return

        tik_total_tokens = 0
        for col in generated_data.columns:
            for value in generated_data[col]:
                tik_total_tokens += count_text_tokens(str(value))

        # Need org/workspace objects for cost logging
        org_id = agent_context.get("organization_id")
        ws_id = agent_context.get("workspace_id")
        if not org_id:
            return

        from accounts.models import Organization, Workspace

        organization = None
        workspace = None
        try:
            organization = Organization.objects.get(id=org_id)
        except Exception:
            pass
        if ws_id:
            try:
                workspace = Workspace.objects.get(id=ws_id)
            except Exception:
                pass

        if organization:
            api_call_type = APICallTypeChoices.SYNTHETIC_DATA_GENERATION.value

            api_call_config = {
                "reference_id": "",
                "is_futureagi_eval": False,
                "input_tokens": int(tik_total_tokens),
            }
            log_and_deduct_cost_for_api_request(
                organization,
                api_call_type,
                config=api_call_config,
                source="synthetic_dataset",
                workspace=workspace,
            )

            # Dual-write: emit cost-based usage event
            try:
                try:
                    from ee.usage.schemas.events import UsageEvent
                except ImportError:
                    UsageEvent = None
                try:
                    from ee.usage.services.config import BillingConfig
                except ImportError:
                    BillingConfig = None
                try:
                    from ee.usage.services.emitter import emit
                except ImportError:
                    emit = None
                try:
                    from ee.usage.utils.event_properties import token_usage_properties
                except ImportError:
                    token_usage_properties = lambda token_usage: {}

                credits = BillingConfig.get().calculate_ai_credits(actual_cost)

                emit(
                    UsageEvent(
                        org_id=str(organization.id),
                        event_type=api_call_type,
                        amount=credits,
                        properties={
                            "source": "synthetic_dataset",
                            "source_id": str(org_id),
                            "raw_cost_usd": str(actual_cost),
                            **token_usage_properties(
                                {"prompt_tokens": int(tik_total_tokens)}
                            ),
                        },
                    )
                )
            except Exception:
                pass  # Metering failure must not break the action

    except Exception as e:
        logger.exception(f"Error deducting cost for api request: {e}")
