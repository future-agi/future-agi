import json
import time
import traceback
from dataclasses import asdict

import structlog
from django.db import transaction
from django.utils import timezone

from accounts.models.workspace import Workspace
from common.utils.data_injection import normalize as _di_normalize

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.fi_evals import *
from model_hub.models.choices import StatusType
from model_hub.models.evals_metric import EvalTemplate
from model_hub.tasks.user_evaluation import trigger_error_localization_for_span
from sdk.utils.helpers import _get_api_call_type
from tfc.temporal import temporal_activity
from tracer.models.custom_eval_config import CustomEvalConfig, EvalOutputType
from tracer.models.eval_task import EvalTask
from tracer.models.observation_span import EvalLogger, EvalTargetType, ObservationSpan
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.utils.helper import FieldConfig, get_default_trace_config
from tracer.views.project import get_default_project_version_config
try:
    from ee.usage.models.usage import APICallStatusChoices
except ImportError:
    APICallStatusChoices = None
try:
    from ee.usage.utils.usage_entries import log_and_deduct_cost_for_api_request
except ImportError:
    log_and_deduct_cost_for_api_request = None

custom_prompt_eval_types = ["CustomPrompt"]
EXPERIMENT = "experiment"
OBSERVE = "observe"

# Re-export for backward compat
from tracer.utils.eval_helpers import resolve_eval_config_id  # noqa: F401, E402

# Friendly eval-mapping shorthands used in saved configs. The user-
# facing variable picker (voice projects in particular) lets people map
# variables to things like ``recording_url`` or ``transcript``; the
# actual span attribute written by the ingestion layer depends on the
# provider — Vapi writes ``conversation.recording.stereo``, the GenAI
# semantic convention path writes ``gen_ai.voice.recording.url``, the
# simulator writes ``stereo_recording_url``, etc. Without a resolver
# here each provider would require a hand-written mapping per span.
# When the exact attribute isn't present we probe these fallbacks in
# order — first match wins.
def _walk_dotted_path(root, path):
    """Walk a dotted path through nested dicts/lists; return None on miss."""
    if not isinstance(path, str) or not path:
        return None
    current = root
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


# Sentinel: ``None`` is a legitimate stored value, so we can't use it for "miss".
_MISSING = object()


def _resolve_attr(span_attrs: dict, candidate: str):
    """Literal lookup, then dotted-path walk. Returns ``_MISSING`` on miss."""
    if candidate in span_attrs:
        return span_attrs[candidate]
    walked = _walk_dotted_path(span_attrs, candidate)
    if walked is not None:
        return walked
    return _MISSING


_ATTRIBUTE_ALIASES: dict[str, list[str]] = {
    "recording_url": [
        # Vapi ingestion (``tracer.utils.vapi._extract_recording_urls``)
        "conversation.recording.stereo",
        "conversation.recording.mono.combined",
        "conversation.recording.mono.customer",
        "conversation.recording.mono.assistant",
        # GenAI semantic convention (``tracer.utils.semantic_conventions``)
        "gen_ai.voice.recording.stereo_url",
        "gen_ai.voice.recording.url",
        # Simulator (``simulate.temporal.activities.xl``)
        "stereo_recording_url",
        "voice_recording_url",
    ],
    "stereo_recording_url": [
        "conversation.recording.stereo",
        "gen_ai.voice.recording.stereo_url",
    ],
    "customer_recording_url": [
        "conversation.recording.mono.customer",
        "gen_ai.voice.recording.customer_url",
    ],
    "assistant_recording_url": [
        "conversation.recording.mono.assistant",
        "gen_ai.voice.recording.assistant_url",
    ],
    "transcript": [
        "conversation.transcript",
        "gen_ai.voice.transcript",
        "voice_transcript",
        "call.transcript",
        "provider_transcript",
    ],
    "call_summary": [
        "conversation.summary",
        "gen_ai.voice.summary",
    ],
}


def _process_mapping(
    mapping: dict | None, span: ObservationSpan, eval_template_id: int
) -> dict:
    """
    Process the mapping from custom eval config to span attributes.

    Uses SpanAttributeAccessor for backward-compatible attribute access,
    supporting both span_attributes (new) and eval_attributes (deprecated).

    Args:
        mapping: Dict mapping eval input keys to span attribute keys
        span: The ObservationSpan to get attributes from
        eval_template_id: The eval template ID for optional key handling

    Returns:
        dict: Parsed mapping with values from span attributes
    """
    from tracer.utils.attribute_accessor import get_span_attributes

    if not mapping:
        return {}

    parsed_mapping = {}
    # Use accessor for backward compatibility (span_attributes || eval_attributes)
    span_attrs = get_span_attributes(span)

    # Handle optional keys from eval template
    try:
        given_eval_template = EvalTemplate.no_workspace_objects.get(id=eval_template_id)
        optional_keys = given_eval_template.config.get("optional_keys", [])
        if len(optional_keys) > 0:
            for key in optional_keys:
                if key in mapping and (mapping[key] is None or mapping[key] == ""):
                    mapping.pop(key)

    except EvalTemplate.DoesNotExist:
        pass

    for key, attribute in mapping.items():
        # Try exact match first, then common fallback patterns.
        # The frontend column picker shows simplified names like "input"
        # but span_attributes often stores them as "input.value". Voice
        # shorthands (``recording_url``, ``transcript``, …) resolve to
        # one of several provider-specific attribute names via the
        # ``_ATTRIBUTE_ALIASES`` table above — first hit wins.
        candidates = [attribute, f"{attribute}.value"]
        for alias in _ATTRIBUTE_ALIASES.get(attribute, []):
            candidates.append(alias)
            candidates.append(f"{alias}.value")

        resolved_value = _MISSING
        for candidate in candidates:
            value = _resolve_attr(span_attrs, candidate)
            if value is not _MISSING:
                resolved_value = value
                break

        if resolved_value is not _MISSING:
            if isinstance(resolved_value, str):
                parsed_mapping[key] = resolved_value
            else:
                parsed_mapping[key] = json.dumps(resolved_value)
        else:
            logger.error(
                f"Required attribute '{attribute}' for key '{key}' not found for span {span.id}"
            )
            raise ValueError(
                f"Required attribute '{attribute}' for key '{key}' not found for span {span.id}"
            )

    return parsed_mapping


def _run_evaluation(
    run_params,
    eval_model,
    eval_instance,
    observation_span,
    custom_eval_config,
    eval_task_id,
    eval_type_id,
    futureagi_eval,
    runner,
    raw_mapping,
    feedback_id=None,
):
    try:
        source_config = {
            "reference_id": observation_span.id,
            "is_futureagi_eval": futureagi_eval,
            "custom_eval_config_id": str(custom_eval_config.id),
        }
        source_config.update(
            {
                "mappings": run_params,
                "required_keys": list(run_params.keys()),
                "span_id": str(observation_span.id),
                "trace_id": str(observation_span.trace.id),
                "source": "tracer",
            }
        )
        if feedback_id:
            source_config.update({"feedback_id": str(feedback_id)})

        api_call_type = _get_api_call_type(custom_eval_config.model)

        workspace = observation_span.project.workspace
        if workspace is None:
            workspace = Workspace.objects.get(
                organization=observation_span.project.organization,
                is_default=True,
                is_active=True,
            )

        # Pre-check: enforce free tier limits
        try:
            from ee.usage.services.metering import check_usage
        except ImportError:
            check_usage = None

        org = observation_span.project.organization
        usage_check = check_usage(str(org.id), api_call_type)
        if not usage_check.allowed:
            raise ValueError(usage_check.reason or "Usage limit exceeded")

        api_call_log_row = log_and_deduct_cost_for_api_request(
            organization=org,
            api_call_type=api_call_type,
            source="tracer" if not feedback_id else "feedback",
            source_id=eval_model.id,
            config=source_config,
            workspace=workspace,
        )
        if not api_call_log_row:
            raise ValueError("API call not allowed : Error validating the api call.")

        if api_call_log_row.status != APICallStatusChoices.PROCESSING.value:
            raise ValueError("API call not allowed : ", api_call_log_row.status)

        start_time = time.time()
        result = eval_instance.run(**run_params)
        end_time = time.time()
        output_type = eval_model.config.get("output", "score")
        response = {
            "data": result.eval_results[0].get("data"),
            "failure": result.eval_results[0].get("failure"),
            "reason": result.eval_results[0].get("reason"),
            "runtime": result.eval_results[0].get("runtime"),
            "model": result.eval_results[0].get("model"),
            "metrics": result.eval_results[0].get("metrics"),
            "metadata": result.eval_results[0].get("metadata"),
            "output": output_type,
            "start_time": start_time,
            "end_time": end_time,
            "duration": end_time - start_time,
        }
        value = runner.format_output(result_data=response, eval_template=eval_model)

        config_dict = json.loads(api_call_log_row.config)
        config_dict.update(
            {
                "input": response["data"],
                "output": {"output": value, "reason": response["reason"]},
            }
        )
        api_call_log_row.config = json.dumps(config_dict)
        api_call_log_row.status = APICallStatusChoices.SUCCESS.value
        api_call_log_row.save()

        # Dual-write: emit usage event for new billing system (cost-based)
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

            actual_cost = getattr(eval_instance, "cost", {}).get("total_cost", 0)
            credits = BillingConfig.get().calculate_ai_credits(actual_cost)

            emit(
                UsageEvent(
                    org_id=str(observation_span.project.organization_id),
                    event_type=api_call_type,
                    amount=credits,
                    properties={
                        "source": "tracer" if not feedback_id else "feedback",
                        "source_id": str(eval_model.id),
                        "model": custom_eval_config.model if custom_eval_config else "",
                        "workspace_id": str(workspace.id) if workspace else "",
                        "log_id": str(api_call_log_row.log_id),
                        "raw_cost_usd": str(actual_cost),
                    },
                )
            )
        except Exception:
            pass  # Metering failure must not break eval

        # Ensure metadata is a dictionary before unpacking
        metadata = result.eval_results[0].get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}

        # Create kwargs dict for EvalLogger based on value type
        logger_kwargs = {
            "trace": observation_span.trace,
            "observation_span": observation_span,
            "output_metadata": {**metadata},
            "eval_explanation": result.eval_results[0].get("reason"),
            "results_explanation": response,
            "eval_task_id": eval_task_id,
            "custom_eval_config": custom_eval_config,
            "eval_type_id": eval_type_id,
            "log_id": api_call_log_row.log_id,
        }

    except Exception as e:
        traceback.print_exc()
        error_message = str(e)
        try:
            api_call_log_row.status = APICallStatusChoices.ERROR.value
            current_config = json.loads(api_call_log_row.config)
            current_config.update({"output": {"output": None, "reason": str(e)}})
            api_call_log_row.config = json.dumps(current_config)
            api_call_log_row.save()
        except Exception:
            pass
        logger_kwargs = {
            "trace": observation_span.trace,
            "observation_span": observation_span,
            "output_metadata": {
                "error": error_message,
                "custom_eval_config_name": custom_eval_config.name,
                "eval_template_name": custom_eval_config.eval_template.name,
            },
            "eval_explanation": f"Error during evaluation: {error_message}",
            "results_explanation": {"reason": error_message},
            "output_str": "ERROR",
            "error": True,
            "error_message": f"Error during evaluation: {error_message}",
            "custom_eval_config": custom_eval_config,
            "eval_type_id": eval_type_id,
            "eval_task_id": eval_task_id,
        }
        value = "ERROR"

    # Determine the appropriate field based on value type
    if value != "ERROR":  # Only try to process value type if no error occurred
        logger_kwargs["value"] = value
        if isinstance(value, bool):
            logger_kwargs["output_bool"] = value
        elif isinstance(value, float) or isinstance(value, int):
            logger_kwargs["output_float"] = float(value)
        elif value in ["Passed", "Failed"]:
            logger_kwargs["output_bool"] = True if value == "Passed" else False
        elif isinstance(value, list):
            logger_kwargs["output_str_list"] = value
        else:
            logger_kwargs["output_str"] = str(value)

    return logger_kwargs


def _execute_composite_on_span(
    observation_span_id,
    custom_eval_config_id,
    eval_task_id,
    run_params=None,
    feedback_id=None,
):
    """Execute a composite `EvalTemplate` against a tracer span.

    Loads the span + custom eval config, resolves the composite's child
    links, and delegates to `execute_composite_children_sync`. Returns a
    `logger_kwargs` dict matching the shape `_execute_evaluation` emits
    for single evals, so the downstream `EvalLogger` writes behave
    identically regardless of composite vs single.
    """
    from model_hub.models.evals_metric import CompositeEvalChild
    from model_hub.utils.composite_execution import execute_composite_children_sync

    try:
        observation_span = ObservationSpan.objects.select_related(
            "project", "project__organization", "project__workspace"
        ).get(id=observation_span_id)
        custom_eval_config = CustomEvalConfig.objects.get(
            id=custom_eval_config_id, deleted=False
        )
    except (ObservationSpan.DoesNotExist, CustomEvalConfig.DoesNotExist) as e:
        raise ValueError(f"Trace composite eval load failed: {e}") from e

    parent = custom_eval_config.eval_template
    org = observation_span.project.organization
    workspace = observation_span.project.workspace

    child_links = list(
        CompositeEvalChild.objects.filter(parent=parent, deleted=False)
        .select_related("child", "pinned_version")
        .order_by("order")
    )
    if not child_links:
        raise ValueError(f"Composite {parent.id} has no children — cannot run on span.")

    try:
        outcome = execute_composite_children_sync(
            parent=parent,
            child_links=child_links,
            mapping=run_params or {},
            config=custom_eval_config.config or {},
            org=org,
            workspace=workspace,
            model=custom_eval_config.model,
            source="tracer_composite",
        )

        value = (
            outcome.aggregate_score
            if parent.aggregation_enabled
            else (outcome.summary or "")
        )
        response = {
            "data": run_params,
            "failure": False,
            "reason": outcome.summary or "",
            "runtime": 0,
            "model": custom_eval_config.model,
            "metrics": None,
            "metadata": {
                "composite_id": str(parent.id),
                "aggregation_enabled": parent.aggregation_enabled,
                "aggregation_function": parent.aggregation_function,
                "aggregate_pass": outcome.aggregate_pass,
                "children": [cr.model_dump() for cr in outcome.child_results],
            },
            "output": "score" if parent.aggregation_enabled else "text",
        }
        logger_kwargs = {
            "trace": observation_span.trace,
            "observation_span": observation_span,
            "output_metadata": response["metadata"],
            "eval_explanation": outcome.summary or "",
            "results_explanation": response,
            "eval_task_id": eval_task_id,
            "custom_eval_config": custom_eval_config,
            "eval_type_id": None,
            "log_id": None,
        }
    except Exception as e:
        traceback.print_exc()
        logger_kwargs = {
            "trace": observation_span.trace,
            "observation_span": observation_span,
            "output_metadata": {
                "error": str(e),
                "composite_id": str(parent.id),
            },
            "eval_explanation": f"Composite eval failed: {e}",
            "results_explanation": {"reason": str(e)},
            "output_str": "ERROR",
            "error": True,
            "error_message": f"Composite eval failed: {e}",
            "custom_eval_config": custom_eval_config,
            "eval_type_id": None,
            "eval_task_id": eval_task_id,
        }
        value = "ERROR"

    if value != "ERROR":
        logger_kwargs["value"] = value
        if isinstance(value, bool):
            logger_kwargs["output_bool"] = value
        elif isinstance(value, float) or isinstance(value, int):
            logger_kwargs["output_float"] = float(value)
        elif isinstance(value, list):
            logger_kwargs["output_str_list"] = value
        else:
            logger_kwargs["output_str"] = str(value)

    return logger_kwargs


def _execute_evaluation(
    observation_span_id,
    custom_eval_config_id,
    eval_task_id,
    type,
    run_params=None,
    feedback_id=None,
):
    from evaluations.constants import FUTUREAGI_EVAL_TYPES
    from evaluations.engine import EvalRequest, run_eval

    raw_mapping = run_params.copy()
    try:
        observation_span = ObservationSpan.objects.select_related(
            "project", "project__organization", "project__workspace"
        ).get(id=observation_span_id)

        custom_eval_config = CustomEvalConfig.objects.get(
            id=custom_eval_config_id, deleted=False
        )
    except ObservationSpan.DoesNotExist:
        raise ValueError("Observation span not found")  # noqa: B904
    except CustomEvalConfig.DoesNotExist:
        raise ValueError("Custom eval config not found")  # noqa: B904
    except Exception:
        raise Exception("Error in _execute_evaluation")  # noqa: B904

    eval_type_id = custom_eval_config.eval_template.config.get("eval_type_id")
    futureagi_eval = eval_type_id in FUTUREAGI_EVAL_TYPES
    eval_model = custom_eval_config.eval_template

    # Composite evals: fan out across children via the shared helper and
    # return a synthesised result that matches the shape downstream
    # logging expects. Single-template execution skips this branch.
    if eval_model.template_type == "composite":
        return _execute_composite_on_span(
            observation_span_id=observation_span_id,
            custom_eval_config_id=custom_eval_config_id,
            eval_task_id=eval_task_id,
            run_params=run_params,
            feedback_id=feedback_id,
        )

    org_id = str(observation_span.project.organization.id)
    ws_id = (
        str(observation_span.project.workspace.id)
        if observation_span.project.workspace
        else None
    )

    # --- Cost tracking (caller-side) ---
    source_config = {
        "reference_id": observation_span.id,
        "is_futureagi_eval": futureagi_eval,
        "custom_eval_config_id": str(custom_eval_config.id),
        "mappings": run_params,
        "required_keys": list(run_params.keys()) if run_params else [],
        "span_id": str(observation_span.id),
        "trace_id": str(observation_span.trace.id),
        "source": "tracer",
    }
    if feedback_id:
        source_config["feedback_id"] = str(feedback_id)

    api_call_type = _get_api_call_type(custom_eval_config.model)
    workspace = observation_span.project.workspace
    if workspace is None:
        workspace = Workspace.objects.get(
            organization=observation_span.project.organization,
            is_default=True,
            is_active=True,
        )

    api_call_log_row = log_and_deduct_cost_for_api_request(
        organization=observation_span.project.organization,
        api_call_type=api_call_type,
        source="tracer" if not feedback_id else "feedback",
        source_id=eval_model.id,
        config=source_config,
        workspace=workspace,
    )
    if not api_call_log_row:
        raise ValueError("API call not allowed : Error validating the api call.")
    if api_call_log_row.status != APICallStatusChoices.PROCESSING.value:
        raise ValueError("API call not allowed : ", api_call_log_row.status)

    # --- Build context for data_injection support ---
    _eval_inputs = dict(run_params or {})
    _di = _di_normalize(
        (custom_eval_config.config or {}).get("run_config", {}).get("data_injection", {})
    )
    if _di["span_context"]:
        _eval_inputs["span_context"] = {
            "id": str(observation_span.id),
            "name": observation_span.name,
            "observation_type": observation_span.observation_type,
            "status": observation_span.status,
            "status_message": observation_span.status_message,
            "model": observation_span.model,
            "latency_ms": observation_span.latency_ms,
            "total_tokens": observation_span.total_tokens,
            "cost": float(observation_span.cost) if observation_span.cost else None,
        }
    if _di["trace_context"]:
        _eval_inputs["trace_context"] = {
            "id": str(observation_span.trace_id),
            "span_id": str(observation_span.id),
        }

    # --- Run eval via unified engine ---
    try:
        result = run_eval(
            EvalRequest(
                eval_template=eval_model,
                inputs=_eval_inputs,
                model=custom_eval_config.model,
                kb_id=(
                    getattr(custom_eval_config.kb_id, "id", custom_eval_config.kb_id)
                    if custom_eval_config.kb_id
                    else None
                ),
                runtime_config=custom_eval_config.config,
                organization_id=org_id,
                workspace_id=ws_id,
            )
        )

        # Update cost log
        config_dict = json.loads(api_call_log_row.config)
        config_dict.update(
            {
                "input": result.data,
                "output": {"output": result.value, "reason": result.reason},
            }
        )
        api_call_log_row.config = json.dumps(config_dict)
        api_call_log_row.status = APICallStatusChoices.SUCCESS.value
        api_call_log_row.save()

        # Parse metadata
        metadata = result.metadata
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}

        value = result.value
        response = {
            "data": result.data,
            "failure": result.failure,
            "reason": result.reason,
            "runtime": result.runtime,
            "model": result.model_used,
            "metrics": result.metrics,
            "metadata": result.metadata,
            "output": result.output_type,
            "start_time": result.start_time,
            "end_time": result.end_time,
            "duration": result.duration,
        }

        logger_kwargs = {
            "trace": observation_span.trace,
            "observation_span": observation_span,
            "output_metadata": {**metadata},
            "eval_explanation": result.reason,
            "results_explanation": response,
            "eval_task_id": eval_task_id,
            "custom_eval_config": custom_eval_config,
            "eval_type_id": eval_type_id,
            "log_id": api_call_log_row.log_id,
        }

    except Exception as e:
        traceback.print_exc()
        error_message = str(e)
        try:
            api_call_log_row.status = APICallStatusChoices.ERROR.value
            current_config = json.loads(api_call_log_row.config)
            current_config.update({"output": {"output": None, "reason": str(e)}})
            api_call_log_row.config = json.dumps(current_config)
            api_call_log_row.save()
        except Exception:
            pass
        logger_kwargs = {
            "trace": observation_span.trace,
            "observation_span": observation_span,
            "output_metadata": {
                "error": error_message,
                "custom_eval_config_name": custom_eval_config.name,
                "eval_template_name": custom_eval_config.eval_template.name,
            },
            "eval_explanation": f"Error during evaluation: {error_message}",
            "results_explanation": {"reason": error_message},
            "output_str": "ERROR",
            "error": True,
            "error_message": f"Error during evaluation: {error_message}",
            "custom_eval_config": custom_eval_config,
            "eval_type_id": eval_type_id,
            "eval_task_id": eval_task_id,
        }
        value = "ERROR"

    # Determine the appropriate field based on value type
    if value != "ERROR":
        logger_kwargs["value"] = value
        if isinstance(value, bool):
            logger_kwargs["output_bool"] = value
        elif isinstance(value, float) or isinstance(value, int):
            logger_kwargs["output_float"] = float(value)
        elif value in ["Passed", "Failed"]:
            logger_kwargs["output_bool"] = True if value == "Passed" else False
        elif isinstance(value, list):
            logger_kwargs["output_str_list"] = value
        else:
            logger_kwargs["output_str"] = str(value)

    # Persist EvalLogger result
    if logger_kwargs:
        value = logger_kwargs.pop("value") if "value" in logger_kwargs else ""
        log_id = logger_kwargs.pop("log_id") if "log_id" in logger_kwargs else None
        try:
            eval_log = EvalLogger.objects.select_related(
                "observation_span",
                "observation_span__project",
                "observation_span__project__organization",
                "observation_span__project__workspace",
            ).get(
                eval_task_id=eval_task_id,
                observation_span=observation_span,
                custom_eval_config=custom_eval_config,
            )
            # Set each attribute from logger_kwargs
            for key, value in logger_kwargs.items():
                setattr(eval_log, key, value)
            # Save the changes
            eval_log.save()

        except EvalLogger.DoesNotExist:
            eval_log = EvalLogger.objects.create(**logger_kwargs)
            eval_log = EvalLogger.objects.select_related(
                "observation_span",
                "observation_span__project",
                "observation_span__project__organization",
                "observation_span__project__workspace",
            ).get(pk=eval_log.pk)

        if custom_eval_config.error_localizer:
            trigger_error_localization_for_span(
                eval_template=eval_model,
                eval_logger=eval_log,
                mapping=raw_mapping,
                eval_explanation=logger_kwargs.get("eval_explanation", ""),
                value=value,
                log_id=str(log_id),
            )

        if type == EXPERIMENT:
            # updating project version config
            project = observation_span.project
            project_version = observation_span.project_version
            project_version_config = project_version.config
            project_config = project.config

            if not project_config:
                project_config = get_default_project_version_config()

            if not project_version_config:
                project_version_config = get_default_trace_config()

            choices = (
                custom_eval_config.eval_template.choices
                if custom_eval_config.eval_template.choices
                else None
            )
            eval_template_config = custom_eval_config.eval_template.config or {}
            output_type = (
                eval_template_config.get("output", "score")
                if eval_template_config
                else "score"
            )

            eval_template_id = str(custom_eval_config.eval_template.id)

            if choices and output_type == EvalOutputType.CHOICES.value:
                for choice in choices:
                    present_config = FieldConfig(
                        id=str(custom_eval_config.id) + "**" + choice,
                        name=f"Avg. {choice} ({custom_eval_config.name})",
                        group_by="Evaluation Metrics",
                        output_type=output_type,
                        is_visible=True,
                        reverse_output=eval_template_config.get(
                            "reverse_output", False
                        ),
                        eval_template_id=eval_template_id,
                    )

                    present_config = asdict(present_config)

                    if present_config not in project_config:
                        project_config.append(present_config)
                    if present_config not in project_version_config:
                        project_version_config.append(present_config)
            else:
                present_config = FieldConfig(
                    id=str(custom_eval_config.id),
                    name=f"Avg. {custom_eval_config.name}",
                    group_by="Evaluation Metrics",
                    output_type=output_type,
                    is_visible=True,
                    reverse_output=eval_template_config.get("reverse_output", False),
                    eval_template_id=eval_template_id,
                )
                present_config = asdict(present_config)
                if present_config not in project_config:
                    project_config.append(present_config)
                if present_config not in project_version_config:
                    project_version_config.append(present_config)

            project.config = project_config
            project_version.config = project_version_config
            project.save()
            project_version.save()


def _create_error_eval_logger(
    observation_span: ObservationSpan,
    custom_eval_config: CustomEvalConfig,
    eval_task_id: str,
    error_message: str,
):
    """
    Create an error eval logger for the given observation span, custom eval config, and eval task id.
    """
    EvalLogger.objects.create(
        trace=observation_span.trace,
        observation_span=observation_span,
        output_metadata={"error": error_message},
        eval_explanation=f"Error during evaluation: {error_message}",
        results_explanation={"reason": error_message},
        eval_task_id=eval_task_id,
        custom_eval_config=custom_eval_config,
        error=True,
        error_message=f"Error during evaluation: {error_message}",
        output_str="ERROR",
    )


@temporal_activity(
    # Retry transient worker / LLM / network failures. The activity is
    # idempotent — an ``EvalLogger.filter(…).exists()`` check early in
    # the body short-circuits re-runs that already succeeded — so a
    # retry never double-writes. ``max_retries=0`` (the prior default)
    # meant any activity in flight during a worker restart or upstream
    # blip was silently dropped with no DLQ; on a 769-span fan-out we
    # observed ~86% of activities vanish across a few worker recycles.
    max_retries=3,
    retry_delay=60,
    time_limit=3600,
    queue="tasks_s",
)
def evaluate_observation_span(
    observation_span_id=None,
    custom_eval_config_id=None,
    feedback_id=None,
):
    if not observation_span_id or not custom_eval_config_id:
        raise ValueError(
            "observation_span_id and custom_eval_config_id are required parameters"
        )

    try:
        custom_eval_config = CustomEvalConfig.objects.get(id=custom_eval_config_id)
        observation_span = ObservationSpan.objects.get(id=observation_span_id)
    except CustomEvalConfig.DoesNotExist:
        raise ValueError(
            f"CustomEvalConfig with id {custom_eval_config_id} does not exist."
        )
    except ObservationSpan.DoesNotExist:
        raise ValueError(
            f"ObservationSpan with id {observation_span_id} does not exist."
        )

    # mark all previous eval_logger as deleted
    EvalLogger.objects.filter(
        observation_span=observation_span, custom_eval_config=custom_eval_config
    ).update(deleted=True, deleted_at=timezone.now())

    try:
        run_params = _process_mapping(
            custom_eval_config.mapping,
            observation_span,
            custom_eval_config.eval_template.id,
        )

        _execute_evaluation(
            observation_span_id=observation_span_id,
            custom_eval_config_id=custom_eval_config_id,
            eval_task_id=None,
            run_params=run_params,
            type=EXPERIMENT,
            feedback_id=feedback_id,
        )
        return True
    except ValueError as e:
        logger.error(f"Error during evaluation in evaluate_observation_span: {e}")
        _create_error_eval_logger(observation_span, custom_eval_config, None, str(e))
        return False

    except Exception as e:
        logger.exception(
            f"Exception during evaluation in evaluate_observation_span: {e}"
        )
        return False


def _write_eval_logger(
    logger_kwargs, observation_span, custom_eval_config, eval_task_id
):
    """Write composite eval results to EvalLogger.

    Composite evals return a logger_kwargs dict from _execute_composite_on_span
    but don't persist it internally (single evals do this in _run_evaluation).
    """
    logger_kwargs.pop("value", None)
    logger_kwargs.pop("log_id", None)
    logger_kwargs.setdefault("trace", observation_span.trace)
    logger_kwargs.setdefault("observation_span", observation_span)
    logger_kwargs.setdefault("custom_eval_config", custom_eval_config)
    logger_kwargs.setdefault("eval_task_id", eval_task_id)
    try:
        EvalLogger.objects.create(**logger_kwargs)
    except Exception as e:
        logger.error(f"Failed to write composite eval logger: {e}")


@temporal_activity(
    # See the retry rationale on ``evaluate_observation_span`` above;
    # this is the per-span activity dispatched by the eval-task cron
    # for observe-mode projects and is the one most exposed to worker
    # recycles during large fan-outs.
    max_retries=3,
    retry_delay=60,
    time_limit=3600,
    queue="tasks_s",
)
def evaluate_observation_span_observe(
    observation_span_id=None,
    custom_eval_config_id=None,
    eval_task_id=None,
    feedback_id=None,
):
    if not observation_span_id or not custom_eval_config_id:
        raise ValueError(
            "observation_span_id and custom_eval_config_id are required parameters"
        )
    try:
        custom_eval_config = CustomEvalConfig.objects.get(id=custom_eval_config_id)
        observation_span = ObservationSpan.objects.get(id=observation_span_id)
    except CustomEvalConfig.DoesNotExist:
        raise ValueError(
            f"CustomEvalConfig with id {custom_eval_config_id} does not exist."
        )
    except ObservationSpan.DoesNotExist:
        raise ValueError(
            f"ObservationSpan with id {observation_span_id} does not exist."
        )

    if EvalLogger.objects.filter(
        observation_span_id=observation_span_id,
        custom_eval_config_id=custom_eval_config_id,
        eval_task_id=eval_task_id,
        deleted=False,
    ).exists():
        logger.info(
            f"EvalLogger with observation_span_id {observation_span_id} and custom_eval_config_id {custom_eval_config_id} already exists for eval task {eval_task_id}."
        )
        return

    # mark all previous eval_logger as deleted
    EvalLogger.objects.filter(
        observation_span=observation_span,
        custom_eval_config=custom_eval_config,
        eval_task_id=eval_task_id,
    ).update(deleted=True, deleted_at=timezone.now())

    try:
        run_params = _process_mapping(
            custom_eval_config.mapping,
            observation_span,
            custom_eval_config.eval_template.id,
        )

        result = _execute_evaluation(
            observation_span_id=observation_span_id,
            custom_eval_config_id=custom_eval_config_id,
            eval_task_id=eval_task_id,
            run_params=run_params,
            type=OBSERVE,
            feedback_id=feedback_id,
        )

        # Composite evals return a logger_kwargs dict instead of writing
        # to EvalLogger internally (single evals do it in _run_evaluation).
        # Persist the composite result here.
        if isinstance(result, dict) and "trace" in result:
            _write_eval_logger(
                result,
                observation_span,
                custom_eval_config,
                eval_task_id,
            )

        # DISABLED 2026-04-30 — per-row enqueue caused Aurora CPU saturation
        # under load (incident: cron-driven historical EvalTask × N×M fan-out
        # → 60+ cluster_eval_results_task/sec → embedding service connection
        # resets → workflow pile-up). Re-enable only after per-project debounce
        # is implemented (Temporal workflow ID dedup or Redis lock).
        #
        # try:
        #     from tracer.tasks.eval_clustering import cluster_eval_results_task
        #
        #     project_id = str(observation_span.project_id)
        #     cluster_eval_results_task.delay(project_id)
        # except Exception:
        #     logger.debug("eval_clustering_dispatch_skipped", exc_info=True)

        return True
    except ValueError as e:
        logger.error(
            f"Error during evaluation in evaluate_observation_span_observe: {e}"
        )
        if eval_task_id:
            try:
                with transaction.atomic():
                    eval_task = EvalTask.objects.select_for_update().get(
                        id=eval_task_id
                    )
                    failed_spans = (
                        eval_task.failed_spans if eval_task.failed_spans else []
                    )

                    failed_spans.append(
                        {
                            "observation_span_id": observation_span_id,
                            "custom_eval_config_id": custom_eval_config_id,
                            "error": str(e),
                        }
                    )

                    eval_task.failed_spans = failed_spans
                    eval_task.save(update_fields=["failed_spans", "updated_at"])
            except EvalTask.DoesNotExist:
                logger.error(f"EvalTask with id {eval_task_id} does not exist.")
            except Exception as e:
                logger.error(
                    f"Error during updating failed spans in exception handling evaluate_observation_span_observe: {e}"
                )
        _create_error_eval_logger(
            observation_span, custom_eval_config, eval_task_id, str(e)
        )

        return False
    except Exception as e:
        logger.exception(
            f"Exception during evaluation in evaluate_observation_span_observe: {e}"
        )
        return False


@temporal_activity(
    # Same rationale as the two activities above — tag-triggered rerun
    # also benefits from idempotent retries.
    max_retries=3,
    retry_delay=60,
    time_limit=3600,
    queue="tasks_s",
)
def eval_observation_span_runner(observation_span_id, eval_tags):
    try:
        observation_span = ObservationSpan.objects.get(id=observation_span_id)
        if not observation_span or not eval_tags:
            return

        if isinstance(eval_tags, str):
            try:
                eval_tags = json.loads(eval_tags)
            except json.JSONDecodeError:
                eval_tags = {}
                logger.warning(
                    "eval_tags JSON decode failed, defaulting to empty dict."
                )

        for eval_tag in eval_tags:
            type = eval_tag.get("type")

            custom_eval_config_id = eval_tag.get("custom_eval_config_id")

            if (
                type == "OBSERVATION_SPAN_TYPE"
                and eval_tag.get("value").lower() == observation_span.observation_type
            ):
                try:
                    evaluate_observation_span(
                        observation_span.id, custom_eval_config_id
                    )
                except Exception as e:
                    custom_eval_config = CustomEvalConfig.objects.get(
                        id=custom_eval_config_id
                    )
                    EvalLogger.objects.create(
                        trace=observation_span.trace,
                        observation_span=observation_span,
                        output_metadata={
                            "error": str(e),
                            "observation_type": observation_span.observation_type,
                        },
                        eval_explanation=f"Error during evaluation: {str(e)}",
                        results_explanation={"reason": str(e)},
                        output_str="ERROR",
                        error=True,
                        error_message=f"Error during evaluation: {str(e)}",
                        custom_eval_config=custom_eval_config,
                    )

        # TODO(tech-debt): Setting eval_status on the span is lossy — it collapses
        # N eval results into one flag. Should be derived from EvalLogger rows instead.
        observation_span.eval_status = StatusType.COMPLETED.value
        observation_span.save()
    except Exception as e:
        traceback.print_exc()
        logger.error(f"Error during evaluation in eval_observation_span_runner: {e}")
        observation_span.eval_status = StatusType.FAILED.value
        observation_span.save()


def score_evals(evals: list):
    """
    Calculate average score for a list of EvalLogger entries.

    Args:
        evals: List of EvalLogger objects
    Returns:
        float: Average score (0-100) or 0 if no valid evaluations
    """
    if not evals:
        return {
            "avg_score": 0,
            "eval_response_data": {},
        }

    total_count = len(evals)
    valid_scores = []
    valid_scores_list = []
    eval_response_data = {}

    for eval_log in evals:
        # if eval_log.eval_id is None:
        #     continue

        custom_eval_config = eval_log.custom_eval_config

        if custom_eval_config and custom_eval_config.id not in eval_response_data:
            eval_response_data[str(custom_eval_config.id)] = {
                "passed_count": 0,
                "failed_count": 0,
                "count": 0,
                "failed_traces_count": 0,
                "failed_traces_ids": [],
                "name": "Low " + custom_eval_config.name,
            }

        eval_response_data[str(custom_eval_config.id)]["count"] += 1
        eval_response_data[str(custom_eval_config.id)]["name"] = (
            "Low " + custom_eval_config.name
        )

        # Handle boolean outputs (Pass/Fail)
        if eval_log.output_bool is not None:
            if eval_log.output_bool:
                valid_scores.append(100)
                eval_response_data[str(custom_eval_config.id)]["passed_count"] += 1
            else:
                valid_scores.append(0)
                eval_response_data[str(custom_eval_config.id)]["failed_count"] += 1
                if (
                    eval_log.trace.id
                    not in eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_ids"
                    ]
                ):
                    eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_count"
                    ] += 1
                    eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_ids"
                    ].append(eval_log.trace.id)
            continue

        # Handle float outputs (direct scores)
        if eval_log.output_float is not None:
            # Ensure score is between 0-100
            score = min(max(eval_log.output_float * 100, 0), 100)
            valid_scores.append(score)
            if score >= 30:
                eval_response_data[str(custom_eval_config.id)]["passed_count"] += 1
            else:
                eval_response_data[str(custom_eval_config.id)]["failed_count"] += 1
                if (
                    eval_log.trace.id
                    not in eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_ids"
                    ]
                ):
                    eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_count"
                    ] += 1
                    eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_ids"
                    ].append(eval_log.trace.id)
            continue

        # Handle string outputs ("Passed"/"Failed")
        if eval_log.output_str:
            if eval_log.output_str.lower() == "passed":
                valid_scores.append(100)
                eval_response_data[str(custom_eval_config.id)]["passed_count"] += 1
            elif (
                eval_log.output_str.lower() == "failed"
                or eval_log.output_str.lower() == "error"
            ):
                valid_scores.append(0)
                eval_response_data[str(custom_eval_config.id)]["failed_count"] += 1
                if (
                    eval_log.trace.id
                    not in eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_ids"
                    ]
                ):
                    eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_count"
                    ] += 1
                    eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_ids"
                    ].append(eval_log.trace.id)
            else:
                valid_scores.append(100)
                eval_response_data[str(custom_eval_config.id)]["passed_count"] += 1

            continue

        if eval_log.output_str_list:
            unique_values = set()
            if isinstance(eval_log.output_str_list, list):
                unique_values.update(eval_log.output_str_list)
                valid_scores_list.extend(list(unique_values))
                valid_scores_list = list(set(valid_scores_list))
                eval_response_data[str(custom_eval_config.id)]["passed_count"] += 1
            else:
                eval_response_data[str(custom_eval_config.id)]["failed_count"] += 1
                if (
                    eval_log.trace.id
                    not in eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_ids"
                    ]
                ):
                    eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_count"
                    ] += 1
                    eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_ids"
                    ].append(eval_log.trace.id)

    # Calculate average score
    if len(valid_scores) > 0:
        return {
            "avg_score": round(sum(valid_scores) / total_count, 2),
            "eval_response_data": eval_response_data,
        }

    if len(valid_scores_list) > 0:
        return {
            "avg_score": valid_scores_list,
            "eval_response_data": eval_response_data,
        }

    return {"avg_score": 0, "eval_response_data": eval_response_data}


def avg_latency(evals: list):
    total_count = 0
    total_latency = 0

    for eval_log in evals:
        try:
            latency = eval_log.latency_ms
            total_latency += latency
            total_count += 1
        except Exception:
            logger.error("ERROR FETCHIHNG LATENCY")
            pass
    if total_count == 0:
        return 0
    return round(total_latency / total_count, 2)


def avg_cost(evals: list):
    total_count = 0
    total_cost = 0

    for eval_log in evals:
        try:
            # cost = eval_log.observation_span.prompt_tokens
            if eval_log.prompt_tokens is not None:
                total_cost += eval_log.prompt_tokens * 0.00000015
            if eval_log.completion_tokens is not None:
                total_cost += eval_log.completion_tokens * 0.0000006
            # total_cost += cost
            total_count += 1
        except Exception:
            logger.error("ERROR FETCHIHNG COST")
            pass
    if total_count == 0:
        return 0
    return round(total_cost / total_count, 2)


def avg_tokens(evals: list):
    total_count = 0
    total_tokens = 0

    for eval_log in evals:
        try:
            tokens = eval_log.total_tokens
            total_tokens += tokens
            total_count += 1
        except Exception:
            logger.error("ERROR FETCHIHNG COST")
            pass
    if total_count == 0:
        return 0
    return round(total_tokens / total_count, 2)


def score_categorical(evals: list, value):
    if not evals:
        return {
            "avg_score": 0,
        }
    passed_count = 0

    total_count = len(evals)

    for eval_log in evals:
        if eval_log.output_str_list:
            if value in eval_log.output_str_list:
                passed_count += 1

    return round(passed_count / total_count, 2) * 100 if total_count > 0 else 0


# ============================================================================
# Trace + session evaluator helpers
# ============================================================================
#
# The trace and session evaluators mirror evaluate_observation_span_observe
# but resolve their mapping variables from a different subject (a Trace or a
# TraceSession instead of an ObservationSpan), and write to EvalLogger with
# different target_type / FK shape:
#
#   target_type='trace'   -> observation_span = trace's root span,
#                            trace = the trace, trace_session = NULL
#   target_type='session' -> observation_span = NULL, trace = NULL,
#                            trace_session = the session
#
# Mapping resolvers walk dotted paths against the subject:
#
#   Trace fields:  ``input``, ``output``, ``name``, ``error``, ``tags``,
#                  ``metadata``, ``external_id``
#   Session fields: ``name``, ``bookmarked``
#   Hierarchy:      ``spans.<n>.<field>`` (n = 0-indexed integer or
#                   ``first``/``last``); for sessions also
#                   ``traces.<n>.spans.<m>.<field>``.
#
# Composite eval support is intentionally span-only here: the helpers raise
# NotImplementedError if a composite template is used. Composite fan-out
# for trace/session subjects is deferred until the UX questions around
# per-target_type composite templates are settled.


# ── Anchor span resolution ──
#
# Trace-level eval rows MUST land with a non-NULL observation_span (per the
# EvalLogger check constraint). The "anchor" is the trace's root span — the one
# whose parent_span_id is NULL. If a trace has no explicit root (anomalous
# data), fall back to the earliest span by start_time. If a trace has zero
# spans, return None — the caller records failure on EvalTask.failed_spans
# and skips the EvalLogger write.


def _find_anchor_span(trace: Trace):
    span = (
        trace.observation_spans.filter(parent_span_id__isnull=True)
        .order_by("start_time", "id")
        .first()
    )
    if span is not None:
        return span
    return trace.observation_spans.order_by("start_time", "id").first()


# ── Path resolution ──
#
# Recursive walker that handles the dot-notation grammar specified in the
# row_type plan: scalar fields, JSONField traversal, indexed/positional
# child collections (``spans.0`` / ``spans.first`` / ``traces.last``), and
# composed paths through children (``traces.0.spans.0.input``).


def _resolve_collection_path(items: list, path: str, item_resolver):
    """Walk into an ordered collection — supports indices and ``first``/``last``."""
    if not path:
        return items
    parts = path.split(".", 1)
    head = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    if head == "first":
        return item_resolver(items[0], rest) if items else _MISSING
    if head == "last":
        return item_resolver(items[-1], rest) if items else _MISSING

    try:
        idx = int(head)
    except ValueError:
        return _MISSING
    if idx < 0 or idx >= len(items):
        return _MISSING
    return item_resolver(items[idx], rest)


# Allow-list of model attributes the trace + session mapping resolvers
# expose. Prevents users from mapping eval inputs to internal Django state
# (``_state``, ``pk``, manager refs, methods, FK-Model objects) and keeps
# the mappable surface a deliberate API contract — not "whatever happens
# to be on the model". When a new field is added to one of these models,
# decide whether it belongs in the eval-mapping surface and update the
# set if so. Span resolution intentionally has no allow-list — it routes
# through the OTel ``span_attributes`` JSONField bag, which is the
# canonical surface the span mapping picker exposes today.
_TRACE_PUBLIC_FIELDS = frozenset(
    {"input", "output", "name", "error", "tags", "metadata", "external_id"}
)
_SESSION_PUBLIC_FIELDS = frozenset({"name", "bookmarked"})


def _resolve_span_path(span: ObservationSpan, path: str):
    """Walk a path against a span via the ``span_attributes`` bag.

    Routes through ``_resolve_attr(span_attrs, path)`` — same surface as the
    pre-existing ``_process_mapping`` resolver, so a saved span mapping that
    works at the span level also works when the path bottoms out at a span
    via ``spans.<n>.<field>`` from a trace or session resolver. The SDK
    mirrors model fields (``input``, ``output``, ``model``, etc.) into
    ``span_attributes`` during ingestion, so users don't lose access to
    them.

    The explicit ``span_attributes`` head case lets a path return the
    whole bag (``spans.0.span_attributes``) or walk into a nested key
    (``spans.0.span_attributes.foo.bar``) without going through the
    aliasing fallback in ``_resolve_attr``.
    """
    from tracer.utils.attribute_accessor import get_span_attributes

    if not path:
        return span

    parts = path.split(".", 1)
    head = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    if head == "span_attributes":
        span_attrs = get_span_attributes(span)
        if not rest:
            return span_attrs
        walked = _walk_dotted_path(span_attrs, rest)
        return walked if walked is not None else _MISSING

    span_attrs = get_span_attributes(span)
    return _resolve_attr(span_attrs, path)


def _resolve_trace_path(trace: Trace, path: str):
    """Walk a path against a trace; supports ``spans.<n>.<field>`` recursion."""
    if not path:
        return trace

    parts = path.split(".", 1)
    head = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    if head in _TRACE_PUBLIC_FIELDS:
        value = getattr(trace, head)
        if not rest:
            return value
        walked = _walk_dotted_path(value, rest)
        return walked if walked is not None else _MISSING

    if head == "spans":
        spans = list(trace.observation_spans.order_by("start_time", "id"))
        return _resolve_collection_path(spans, rest, _resolve_span_path)

    return _MISSING


def _resolve_session_path(trace_session: TraceSession, path: str):
    """Walk a path against a session; supports ``traces.<n>.spans.<m>.<field>``."""
    if not path:
        return trace_session

    parts = path.split(".", 1)
    head = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    if head in _SESSION_PUBLIC_FIELDS:
        value = getattr(trace_session, head)
        if not rest:
            return value
        walked = _walk_dotted_path(value, rest)
        return walked if walked is not None else _MISSING

    if head == "traces":
        # Match the trace-listing UI's ordering (``list_traces_of_session``
        # in ``tracer/views/trace.py``): earliest root span's ``start_time``,
        # falling back to ``created_at`` when no root span has landed yet.
        # Without this, sessions whose traces share a ``created_at`` (the
        # SDK stamps every trace in a run with the same instant) tie-break
        # by id alphabetically -- picking a "trace 0" the user never sees
        # at the top of the trace list.
        from django.db.models import OuterRef, Subquery
        from django.db.models.functions import Coalesce

        root_start = (
            ObservationSpan.objects.filter(
                trace_id=OuterRef("id"), parent_span_id__isnull=True
            )
            .order_by("start_time")
            .values("start_time")[:1]
        )
        traces = list(
            trace_session.traces.annotate(
                _root_start=Coalesce(Subquery(root_start), "created_at")
            ).order_by("_root_start", "id")
        )
        return _resolve_collection_path(traces, rest, _resolve_trace_path)

    return _MISSING


def _process_trace_mapping(
    mapping: dict | None, trace: Trace, eval_template_id
) -> dict:
    """Resolve a saved mapping against a Trace.

    Mirrors ``_process_mapping`` (the span resolver) but walks the trace
    path grammar: trace fields, child-span aggregators, and dotted paths
    into ``spans.<n>.<field>``. Raises ``ValueError`` on a required-key
    miss so the caller writes an error EvalLogger row and continues.
    """
    if not mapping:
        return {}

    parsed: dict = {}

    try:
        given_eval_template = EvalTemplate.no_workspace_objects.get(
            id=eval_template_id
        )
        optional_keys = given_eval_template.config.get("optional_keys", []) or []
        for key in optional_keys:
            if key in mapping and (mapping[key] is None or mapping[key] == ""):
                mapping.pop(key)
    except EvalTemplate.DoesNotExist:
        pass

    for key, attribute in mapping.items():
        value = _resolve_trace_path(trace, attribute) if attribute else _MISSING
        if value is _MISSING:
            logger.error(
                f"Required attribute '{attribute}' for key '{key}' not found "
                f"on trace {trace.id}"
            )
            raise ValueError(
                f"Required attribute '{attribute}' for key '{key}' not found "
                f"on trace {trace.id}"
            )
        parsed[key] = value if isinstance(value, str) else json.dumps(value)

    return parsed


def _process_session_mapping(
    mapping: dict | None, trace_session: TraceSession, eval_template_id
) -> dict:
    """Resolve a saved mapping against a TraceSession."""
    if not mapping:
        return {}

    parsed: dict = {}

    try:
        given_eval_template = EvalTemplate.no_workspace_objects.get(
            id=eval_template_id
        )
        optional_keys = given_eval_template.config.get("optional_keys", []) or []
        for key in optional_keys:
            if key in mapping and (mapping[key] is None or mapping[key] == ""):
                mapping.pop(key)
    except EvalTemplate.DoesNotExist:
        pass

    for key, attribute in mapping.items():
        value = (
            _resolve_session_path(trace_session, attribute)
            if attribute
            else _MISSING
        )
        if value is _MISSING:
            logger.error(
                f"Required attribute '{attribute}' for key '{key}' not found "
                f"on session {trace_session.id}"
            )
            raise ValueError(
                f"Required attribute '{attribute}' for key '{key}' not found "
                f"on session {trace_session.id}"
            )
        parsed[key] = value if isinstance(value, str) else json.dumps(value)

    return parsed


# ── Eval execution: trace ──


def _execute_evaluation_for_trace(
    *,
    trace: Trace,
    anchor_span: ObservationSpan,
    custom_eval_config: CustomEvalConfig,
    eval_task_id,
    run_params: dict,
    feedback_id=None,
):
    """Run the eval engine against a trace + persist the EvalLogger row.

    Twin of ``_execute_evaluation`` — same flow (cost log → run_eval → write
    logger), but resolves project/org/workspace off the trace and writes
    a target_type='trace' row anchored to ``anchor_span``. Composite
    templates raise ``NotImplementedError`` (span-only).
    """
    from evaluations.constants import FUTUREAGI_EVAL_TYPES
    from evaluations.engine import EvalRequest, run_eval

    eval_template = custom_eval_config.eval_template
    if eval_template.template_type == "composite":
        raise NotImplementedError(
            "Composite eval templates are span-only. Trace-level "
            "composite fan-out is not supported yet."
        )
    eval_type_id = eval_template.config.get("eval_type_id")
    futureagi_eval = eval_type_id in FUTUREAGI_EVAL_TYPES

    org_id = str(trace.project.organization.id)
    workspace = trace.project.workspace
    if workspace is None:
        workspace = Workspace.objects.get(
            organization=trace.project.organization,
            is_default=True,
            is_active=True,
        )
    ws_id = str(workspace.id) if workspace else None

    source_config = {
        "reference_id": str(trace.id),
        "is_futureagi_eval": futureagi_eval,
        "custom_eval_config_id": str(custom_eval_config.id),
        "mappings": run_params,
        "required_keys": list(run_params.keys()) if run_params else [],
        "trace_id": str(trace.id),
        "span_id": str(anchor_span.id),
        "target_type": EvalTargetType.TRACE.value,
        "source": "tracer",
    }
    if feedback_id:
        source_config["feedback_id"] = str(feedback_id)

    api_call_type = _get_api_call_type(custom_eval_config.model)
    api_call_log_row = log_and_deduct_cost_for_api_request(
        organization=trace.project.organization,
        api_call_type=api_call_type,
        source="tracer" if not feedback_id else "feedback",
        source_id=eval_template.id,
        config=source_config,
        workspace=workspace,
    )
    if not api_call_log_row:
        raise ValueError("API call not allowed : Error validating the api call.")
    if api_call_log_row.status != APICallStatusChoices.PROCESSING.value:
        raise ValueError("API call not allowed : ", api_call_log_row.status)

    try:
        result = run_eval(
            EvalRequest(
                eval_template=eval_template,
                inputs=run_params or {},
                model=custom_eval_config.model,
                kb_id=(
                    getattr(custom_eval_config.kb_id, "id", custom_eval_config.kb_id)
                    if custom_eval_config.kb_id
                    else None
                ),
                runtime_config=custom_eval_config.config,
                organization_id=org_id,
                workspace_id=ws_id,
            )
        )

        config_dict = json.loads(api_call_log_row.config)
        config_dict.update(
            {
                "input": result.data,
                "output": {"output": result.value, "reason": result.reason},
            }
        )
        api_call_log_row.config = json.dumps(config_dict)
        api_call_log_row.status = APICallStatusChoices.SUCCESS.value
        api_call_log_row.save()

        metadata = result.metadata
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}

        value = result.value
        response = {
            "data": result.data,
            "failure": result.failure,
            "reason": result.reason,
            "runtime": result.runtime,
            "model": result.model_used,
            "metrics": result.metrics,
            "metadata": result.metadata,
            "output": result.output_type,
            "start_time": result.start_time,
            "end_time": result.end_time,
            "duration": result.duration,
        }
        logger_kwargs = {
            "target_type": EvalTargetType.TRACE.value,
            "trace": trace,
            "observation_span": anchor_span,
            "trace_session": None,
            "output_metadata": {**metadata},
            "eval_explanation": result.reason,
            "results_explanation": response,
            "eval_task_id": eval_task_id,
            "custom_eval_config": custom_eval_config,
            "eval_type_id": eval_type_id,
        }
    except Exception as e:
        traceback.print_exc()
        error_message = str(e)
        try:
            api_call_log_row.status = APICallStatusChoices.ERROR.value
            current_config = json.loads(api_call_log_row.config)
            current_config.update(
                {"output": {"output": None, "reason": str(e)}}
            )
            api_call_log_row.config = json.dumps(current_config)
            api_call_log_row.save()
        except Exception:
            pass
        logger_kwargs = {
            "target_type": EvalTargetType.TRACE.value,
            "trace": trace,
            "observation_span": anchor_span,
            "trace_session": None,
            "output_metadata": {
                "error": error_message,
                "custom_eval_config_name": custom_eval_config.name,
                "eval_template_name": eval_template.name,
            },
            "eval_explanation": f"Error during evaluation: {error_message}",
            "results_explanation": {"reason": error_message},
            "output_str": "ERROR",
            "error": True,
            "error_message": f"Error during evaluation: {error_message}",
            "custom_eval_config": custom_eval_config,
            "eval_type_id": eval_type_id,
            "eval_task_id": eval_task_id,
        }
        value = "ERROR"

    if value != "ERROR":
        if isinstance(value, bool):
            logger_kwargs["output_bool"] = value
        elif isinstance(value, float) or isinstance(value, int):
            logger_kwargs["output_float"] = float(value)
        elif value in ["Passed", "Failed"]:
            logger_kwargs["output_bool"] = True if value == "Passed" else False
        elif isinstance(value, list):
            logger_kwargs["output_str_list"] = value
        else:
            logger_kwargs["output_str"] = str(value)

    EvalLogger.objects.create(**logger_kwargs)


def _execute_evaluation_for_session(
    *,
    trace_session: TraceSession,
    custom_eval_config: CustomEvalConfig,
    eval_task_id,
    run_params: dict,
    feedback_id=None,
):
    """Twin of ``_execute_evaluation_for_trace`` but for sessions."""
    from evaluations.constants import FUTUREAGI_EVAL_TYPES
    from evaluations.engine import EvalRequest, run_eval

    eval_template = custom_eval_config.eval_template
    if eval_template.template_type == "composite":
        raise NotImplementedError(
            "Composite eval templates are span-only. Session-level "
            "composite fan-out is not supported yet."
        )
    eval_type_id = eval_template.config.get("eval_type_id")
    futureagi_eval = eval_type_id in FUTUREAGI_EVAL_TYPES

    org_id = str(trace_session.project.organization.id)
    workspace = trace_session.project.workspace
    if workspace is None:
        workspace = Workspace.objects.get(
            organization=trace_session.project.organization,
            is_default=True,
            is_active=True,
        )
    ws_id = str(workspace.id) if workspace else None

    source_config = {
        "reference_id": str(trace_session.id),
        "is_futureagi_eval": futureagi_eval,
        "custom_eval_config_id": str(custom_eval_config.id),
        "mappings": run_params,
        "required_keys": list(run_params.keys()) if run_params else [],
        "session_id": str(trace_session.id),
        "target_type": EvalTargetType.SESSION.value,
        "source": "tracer",
    }
    if feedback_id:
        source_config["feedback_id"] = str(feedback_id)

    api_call_type = _get_api_call_type(custom_eval_config.model)
    api_call_log_row = log_and_deduct_cost_for_api_request(
        organization=trace_session.project.organization,
        api_call_type=api_call_type,
        source="tracer" if not feedback_id else "feedback",
        source_id=eval_template.id,
        config=source_config,
        workspace=workspace,
    )
    if not api_call_log_row:
        raise ValueError("API call not allowed : Error validating the api call.")
    if api_call_log_row.status != APICallStatusChoices.PROCESSING.value:
        raise ValueError("API call not allowed : ", api_call_log_row.status)

    try:
        result = run_eval(
            EvalRequest(
                eval_template=eval_template,
                inputs=run_params or {},
                model=custom_eval_config.model,
                kb_id=(
                    getattr(custom_eval_config.kb_id, "id", custom_eval_config.kb_id)
                    if custom_eval_config.kb_id
                    else None
                ),
                runtime_config=custom_eval_config.config,
                organization_id=org_id,
                workspace_id=ws_id,
            )
        )

        config_dict = json.loads(api_call_log_row.config)
        config_dict.update(
            {
                "input": result.data,
                "output": {"output": result.value, "reason": result.reason},
            }
        )
        api_call_log_row.config = json.dumps(config_dict)
        api_call_log_row.status = APICallStatusChoices.SUCCESS.value
        api_call_log_row.save()

        metadata = result.metadata
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}

        value = result.value
        response = {
            "data": result.data,
            "failure": result.failure,
            "reason": result.reason,
            "runtime": result.runtime,
            "model": result.model_used,
            "metrics": result.metrics,
            "metadata": result.metadata,
            "output": result.output_type,
            "start_time": result.start_time,
            "end_time": result.end_time,
            "duration": result.duration,
        }
        logger_kwargs = {
            "target_type": EvalTargetType.SESSION.value,
            "trace": None,
            "observation_span": None,
            "trace_session": trace_session,
            "output_metadata": {**metadata},
            "eval_explanation": result.reason,
            "results_explanation": response,
            "eval_task_id": eval_task_id,
            "custom_eval_config": custom_eval_config,
            "eval_type_id": eval_type_id,
        }
    except Exception as e:
        traceback.print_exc()
        error_message = str(e)
        try:
            api_call_log_row.status = APICallStatusChoices.ERROR.value
            current_config = json.loads(api_call_log_row.config)
            current_config.update(
                {"output": {"output": None, "reason": str(e)}}
            )
            api_call_log_row.config = json.dumps(current_config)
            api_call_log_row.save()
        except Exception:
            pass
        logger_kwargs = {
            "target_type": EvalTargetType.SESSION.value,
            "trace": None,
            "observation_span": None,
            "trace_session": trace_session,
            "output_metadata": {
                "error": error_message,
                "custom_eval_config_name": custom_eval_config.name,
                "eval_template_name": eval_template.name,
            },
            "eval_explanation": f"Error during evaluation: {error_message}",
            "results_explanation": {"reason": error_message},
            "output_str": "ERROR",
            "error": True,
            "error_message": f"Error during evaluation: {error_message}",
            "custom_eval_config": custom_eval_config,
            "eval_type_id": eval_type_id,
            "eval_task_id": eval_task_id,
        }
        value = "ERROR"

    if value != "ERROR":
        if isinstance(value, bool):
            logger_kwargs["output_bool"] = value
        elif isinstance(value, float) or isinstance(value, int):
            logger_kwargs["output_float"] = float(value)
        elif value in ["Passed", "Failed"]:
            logger_kwargs["output_bool"] = True if value == "Passed" else False
        elif isinstance(value, list):
            logger_kwargs["output_str_list"] = value
        else:
            logger_kwargs["output_str"] = str(value)

    EvalLogger.objects.create(**logger_kwargs)


# ── Error helpers ──


def _create_error_eval_logger_for_trace(
    trace: Trace,
    anchor_span: ObservationSpan,
    custom_eval_config: CustomEvalConfig,
    eval_task_id,
    error_message: str,
):
    """Persist a target_type='trace' EvalLogger row with error=True."""
    EvalLogger.objects.create(
        target_type=EvalTargetType.TRACE.value,
        trace=trace,
        observation_span=anchor_span,
        trace_session=None,
        output_metadata={"error": error_message},
        eval_explanation=f"Error during evaluation: {error_message}",
        results_explanation={"reason": error_message},
        eval_task_id=eval_task_id,
        custom_eval_config=custom_eval_config,
        error=True,
        error_message=f"Error during evaluation: {error_message}",
        output_str="ERROR",
    )


def _create_error_eval_logger_for_session(
    trace_session: TraceSession,
    custom_eval_config: CustomEvalConfig,
    eval_task_id,
    error_message: str,
):
    """Persist a target_type='session' EvalLogger row with error=True."""
    EvalLogger.objects.create(
        target_type=EvalTargetType.SESSION.value,
        trace=None,
        observation_span=None,
        trace_session=trace_session,
        output_metadata={"error": error_message},
        eval_explanation=f"Error during evaluation: {error_message}",
        results_explanation={"reason": error_message},
        eval_task_id=eval_task_id,
        custom_eval_config=custom_eval_config,
        error=True,
        error_message=f"Error during evaluation: {error_message}",
        output_str="ERROR",
    )


# ── Temporal activities ──


@temporal_activity(
    max_retries=3,
    retry_delay=60,
    time_limit=3600,
    queue="tasks_s",
)
def evaluate_trace_observe(
    trace_id=None,
    custom_eval_config_id=None,
    eval_task_id=None,
    feedback_id=None,
):
    """Per-trace evaluator dispatched by ``process_eval_task`` for row_type=traces.

    Mirrors ``evaluate_observation_span_observe`` but scoped to a Trace:
    look up the trace + eval config, idempotency-check on
    ``(trace_id, target_type='trace', eval_config, eval_task)``, soft-delete
    any prior attempts for the same triple, resolve mapping variables off
    the trace via ``_process_trace_mapping``, run the engine, write a
    target_type='trace' EvalLogger row anchored to the trace's root span.
    """
    if not trace_id or not custom_eval_config_id:
        raise ValueError(
            "trace_id and custom_eval_config_id are required parameters"
        )

    try:
        custom_eval_config = CustomEvalConfig.objects.get(id=custom_eval_config_id)
        trace = Trace.objects.select_related(
            "project", "project__organization", "project__workspace"
        ).get(id=trace_id)
    except CustomEvalConfig.DoesNotExist:
        raise ValueError(
            f"CustomEvalConfig with id {custom_eval_config_id} does not exist."
        )
    except Trace.DoesNotExist:
        raise ValueError(f"Trace with id {trace_id} does not exist.")

    # Idempotency: the dispatcher writes one row per (trace, eval_config, task).
    # ``eval_task_id`` already scopes the check to this task's row_type — every
    # row sharing this eval_task_id is target_type='trace' by construction
    # (the dispatcher dispatched this activity because EvalTask.row_type='traces').
    if EvalLogger.objects.filter(
        trace_id=trace_id,
        custom_eval_config_id=custom_eval_config_id,
        eval_task_id=eval_task_id,
        deleted=False,
    ).exists():
        logger.info(
            f"EvalLogger (target_type=trace) for trace_id {trace_id} and "
            f"custom_eval_config_id {custom_eval_config_id} already exists "
            f"for eval task {eval_task_id}."
        )
        return

    anchor_span = _find_anchor_span(trace)
    if anchor_span is None:
        # Trace has zero spans — can't write a trace EvalLogger row (the
        # check constraint forbids target_type='trace' with NULL span).
        # Record the failure on EvalTask.failed_spans and bail.
        if eval_task_id:
            try:
                with transaction.atomic():
                    eval_task = EvalTask.objects.select_for_update().get(
                        id=eval_task_id
                    )
                    failed = list(eval_task.failed_spans or [])
                    failed.append(
                        {
                            "trace_id": str(trace_id),
                            "custom_eval_config_id": str(custom_eval_config_id),
                            "error": (
                                f"Trace {trace_id} has zero spans — "
                                "cannot anchor a trace-level eval result."
                            ),
                        }
                    )
                    eval_task.failed_spans = failed
                    eval_task.save(update_fields=["failed_spans", "updated_at"])
            except Exception as save_err:
                logger.error(
                    f"Failed to record zero-span trace failure on eval task: {save_err}"
                )
        return False

    try:
        run_params = _process_trace_mapping(
            custom_eval_config.mapping,
            trace,
            custom_eval_config.eval_template.id,
        )
        _execute_evaluation_for_trace(
            trace=trace,
            anchor_span=anchor_span,
            custom_eval_config=custom_eval_config,
            eval_task_id=eval_task_id,
            run_params=run_params,
            feedback_id=feedback_id,
        )
        return True
    except ValueError as e:
        logger.error(f"Error during evaluation in evaluate_trace_observe: {e}")
        if eval_task_id:
            try:
                with transaction.atomic():
                    eval_task = EvalTask.objects.select_for_update().get(
                        id=eval_task_id
                    )
                    failed = list(eval_task.failed_spans or [])
                    failed.append(
                        {
                            "trace_id": str(trace_id),
                            "custom_eval_config_id": str(custom_eval_config_id),
                            "error": str(e),
                        }
                    )
                    eval_task.failed_spans = failed
                    eval_task.save(update_fields=["failed_spans", "updated_at"])
            except EvalTask.DoesNotExist:
                logger.error(f"EvalTask with id {eval_task_id} does not exist.")
            except Exception as save_err:
                logger.error(
                    f"Error updating failed_spans during trace eval error: {save_err}"
                )
        _create_error_eval_logger_for_trace(
            trace, anchor_span, custom_eval_config, eval_task_id, str(e)
        )
        return False
    except Exception as e:
        logger.exception(
            f"Exception during evaluation in evaluate_trace_observe: {e}"
        )
        return False


@temporal_activity(
    max_retries=3,
    retry_delay=60,
    time_limit=3600,
    queue="tasks_s",
)
def evaluate_trace_session_observe(
    session_id=None,
    custom_eval_config_id=None,
    eval_task_id=None,
    feedback_id=None,
):
    """Per-session evaluator dispatched by ``process_eval_task`` for row_type=sessions.

    Mirrors ``evaluate_trace_observe`` but scoped to a TraceSession.
    Writes a target_type='session' EvalLogger row with NULL span/trace
    and the session FK populated.
    """
    if not session_id or not custom_eval_config_id:
        raise ValueError(
            "session_id and custom_eval_config_id are required parameters"
        )

    try:
        custom_eval_config = CustomEvalConfig.objects.get(id=custom_eval_config_id)
        trace_session = TraceSession.objects.select_related(
            "project", "project__organization", "project__workspace"
        ).get(id=session_id)
    except CustomEvalConfig.DoesNotExist:
        raise ValueError(
            f"CustomEvalConfig with id {custom_eval_config_id} does not exist."
        )
    except TraceSession.DoesNotExist:
        raise ValueError(f"TraceSession with id {session_id} does not exist.")

    # Same idempotency rationale as the trace evaluator: eval_task_id alone
    # scopes the check to this task's row_type (target_type='session'), so a
    # redundant target_type filter would be a tautology.
    if EvalLogger.objects.filter(
        trace_session_id=session_id,
        custom_eval_config_id=custom_eval_config_id,
        eval_task_id=eval_task_id,
        deleted=False,
    ).exists():
        logger.info(
            f"EvalLogger (target_type=session) for session_id {session_id} "
            f"and custom_eval_config_id {custom_eval_config_id} already "
            f"exists for eval task {eval_task_id}."
        )
        return

    try:
        run_params = _process_session_mapping(
            custom_eval_config.mapping,
            trace_session,
            custom_eval_config.eval_template.id,
        )
        _execute_evaluation_for_session(
            trace_session=trace_session,
            custom_eval_config=custom_eval_config,
            eval_task_id=eval_task_id,
            run_params=run_params,
            feedback_id=feedback_id,
        )
        return True
    except ValueError as e:
        logger.error(
            f"Error during evaluation in evaluate_trace_session_observe: {e}"
        )
        if eval_task_id:
            try:
                with transaction.atomic():
                    eval_task = EvalTask.objects.select_for_update().get(
                        id=eval_task_id
                    )
                    failed = list(eval_task.failed_spans or [])
                    failed.append(
                        {
                            "session_id": str(session_id),
                            "custom_eval_config_id": str(custom_eval_config_id),
                            "error": str(e),
                        }
                    )
                    eval_task.failed_spans = failed
                    eval_task.save(update_fields=["failed_spans", "updated_at"])
            except EvalTask.DoesNotExist:
                logger.error(f"EvalTask with id {eval_task_id} does not exist.")
            except Exception as save_err:
                logger.error(
                    f"Error updating failed_spans during session eval error: {save_err}"
                )
        _create_error_eval_logger_for_session(
            trace_session, custom_eval_config, eval_task_id, str(e)
        )
        return False
    except Exception as e:
        logger.exception(
            f"Exception during evaluation in evaluate_trace_session_observe: {e}"
        )
        return False
