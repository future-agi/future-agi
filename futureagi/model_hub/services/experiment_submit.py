"""Commit experiment submission intent before admitting retryable execution."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from functools import wraps

import structlog
from django.db import transaction
from rest_framework.response import Response

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import ExperimentsTable
from model_hub.services import experiment_target_admission as target_admission

STATE_KEY = "_experiment_v2_submission"
logger = structlog.get_logger(__name__)


@dataclass
class Submission:
    experiment_id: str
    response: Response


def request_hash(data, operation):
    canonical = json.dumps(
        [operation, data], sort_keys=True, default=str, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def state_of(experiment):
    if not experiment.snapshot_dataset_id:
        return {}
    config = (
        Dataset.no_workspace_objects.filter(id=experiment.snapshot_dataset_id)
        .values_list("dataset_config", flat=True)
        .first()
    )
    return (config or {}).get(STATE_KEY, {})


def matching_submission(experiment, data, operation):
    """Recognize retries and refuse edits over unresolved or running work."""
    state = state_of(experiment)
    if state.get("request_hash") == request_hash(data, operation):
        return True
    if state.get("state") == "pending":
        raise ValueError(
            "An earlier submission is unconfirmed. Retry that submission before editing."
        )
    if experiment.status in (StatusType.QUEUED.value, StatusType.RUNNING.value):
        raise ValueError(
            "Wait for the current execution to finish before changing its configuration."
        )
    return False


def stage_submission(experiment, data, operation, **workflow_args):
    """Persist intent in the mutation transaction with the experiment locked."""
    snapshot = Dataset.no_workspace_objects.select_for_update().get(
        id=experiment.snapshot_dataset_id
    )
    config = dict(snapshot.dataset_config or {})
    config[STATE_KEY] = {
        "admission_id": str(uuid.uuid4()),
        "request_hash": request_hash(data, operation),
        "state": "pending",
        "workflow_args": workflow_args,
    }
    snapshot.dataset_config = config
    snapshot.save(update_fields=["dataset_config"])
    experiment.status = StatusType.QUEUED.value
    experiment.save(update_fields=["status"])


def admit_submission(experiment_id):
    """Serialize retries while retaining committed intent after ambiguous RPCs."""
    from tfc.temporal.experiments import start_experiment_v2_workflow

    with transaction.atomic():
        experiment = ExperimentsTable.no_workspace_objects.select_for_update().get(
            id=experiment_id
        )
        snapshot = Dataset.no_workspace_objects.select_for_update().get(
            id=experiment.snapshot_dataset_id
        )
        config = dict(snapshot.dataset_config or {})
        state = dict(config.get(STATE_KEY, {}))
        if state.get("state") == "accepted":
            return
        if not state.get("admission_id"):
            raise ValueError("Submission intent is missing.")
        # TX1-B: the staged selectors decide which agent targets would run.
        args = state["workflow_args"]
        target_admission.ensure_v2_run_admitted(
            str(experiment.id),
            rerun_prompt_config_ids=args.get("rerun_prompt_config_ids"),
            rerun_agent_config_ids=args.get("rerun_agent_config_ids"),
            rerun_eval_template_ids=args.get("rerun_eval_template_ids"),
            column_changed=args.get("column_changed", False),
        )
        workflow_id = start_experiment_v2_workflow(
            experiment_id=str(experiment.id),
            admission_id=state["admission_id"],
            **state["workflow_args"],
        )
        if not workflow_id:
            raise ValueError("Execution admission was not confirmed.")
        state.update(state="accepted", workflow_id=workflow_id)
        config[STATE_KEY] = state
        snapshot.dataset_config = config
        snapshot.save(update_fields=["dataset_config"])


def submit_transaction(method):
    """Commit mutation and intent together, then report confirmed admission."""

    @wraps(method)
    def wrapped(*args, **kwargs):
        if transaction.get_connection().settings_dict.get("ATOMIC_REQUESTS"):
            return Response(
                {
                    "status": False,
                    "detail": "Experiment submission requires autocommit.",
                },
                status=503,
            )
        with transaction.atomic():
            result = method(*args, **kwargs)
            if not isinstance(result, Submission):
                if result.status_code >= 400:
                    transaction.set_rollback(True)
                return result
        try:
            admit_submission(result.experiment_id)
        except target_admission.AgentTargetNotQualified as error:
            # A policy refusal is a 403, never an unconfirmed admission.
            return target_admission.refusal_response(error)
        except Exception as error:
            logger.warning(
                "Experiment admission unconfirmed",
                experiment_id=result.experiment_id,
                error_type=type(error).__name__,
            )
            return Response(
                {
                    "status": False,
                    "code": "experiment_admission_unconfirmed",
                    "detail": (
                        "Configuration saved, but execution admission is unconfirmed. "
                        "Retry the same submission; do not create another experiment. "
                        f"experiment_id={result.experiment_id}"
                    ),
                },
                status=503,
            )
        return result.response

    return wrapped
