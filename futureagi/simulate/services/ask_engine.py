"""The engine step of an ask: one grading for each call and question.

A person who has seen the check before launch (``ask_selection``) launches the
ask. The launch:
- saves one eval template per typed question, the way Instant Eval saves one
  (its tag, its model, its shape), so they appear in the organisation's eval
  library as Instant Eval's do;
- writes the ask and one pending result row per call and question;
- queues one job.

The job grades each row once, through the shared evaluator's entry
(``run_eval_func``), which checks usage, writes its own log row and bills. At
most ``MAX_GRADING_AT_ONCE_PER_ORG`` gradings run at once for one organisation.

Rules (ADR-018, section 6):
- It writes only its result rows: nothing on the call or the run, and it queues
  no summary.
- Every input is named in the criteria as sent, so the evaluator passes each
  value whole. An input the model would not get whole is ``unmeasured``
  (``too_large``) and never sent.
- At most once: a task claims its row, committed, before it calls the
  evaluator. A second delivery finds it claimed and does nothing. A row whose
  worker died becomes ``infrastructure_error`` and is not run again.
- Errors are never fails: the evaluator failing is ``grading_error``, the queue
  refusing is ``infrastructure_error``, and a denial or credits running out
  stops the ask, its gradings not yet run ``cancelled``.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from typing import Any

import structlog
from django.db import connection, transaction
from django.utils import timezone

from model_hub.views.utils.evals import run_eval_func
from simulate.models import CallExecution, SimulateAsk, SimulateEvalResult
from simulate.services.ask_selection import (
    FrozenSelection,
    LaunchRefused,
    check_before_launch,
    launch_gate,
)
from simulate.services.call_evidence import (
    READER_VERSION,
    CallDeleted,
    CallNotInWorkspace,
    Kind,
    State,
    read_call_evidence,
)
from tfc.temporal.drop_in import temporal_activity
from tfc.utils.evaluation_denials import EvaluationDenied

logger = structlog.get_logger(__name__)

ENGINE = "simulate_ask_step"
# Instant Eval's own model and tag (frontend InstantEval/instantEvalPayloads.js:
# INSTANT_EVAL_MODEL, INSTANT_EVAL_TAG). A typed question is saved as it saves one.
TYPED_QUESTION_MODEL = "jev-latest"
TEMPLATE_TAG = "instant-eval"
# Instant Eval's limits on a question (the same file).
MAX_QUESTION_LENGTH = 2000
MAX_CATEGORIES = 10
MAX_LABEL_LENGTH = 200
MAX_DEFINITION_LENGTH = 2000
YES_NO_LABELS = ("Yes", "No")
# Gradings at once for one organisation (a guessed limit), so an ask cannot
# crowd that organisation's own Instant Eval runs or use up its key's limits.
MAX_GRADING_AT_ONCE_PER_ORG = 4
# Threads one ask's job grades with. It never exceeds the limit above.
GRADING_THREADS = MAX_GRADING_AT_ONCE_PER_ORG
# A claimed row with no answer after this is taken to have lost its worker. The
# decision model's own deadline is 90 seconds, retries included.
GRADING_GIVE_UP = timedelta(minutes=30)
# How long a grading waits for a free place under the organisation's limit.
BUSY_WAIT_SECONDS = 2
# The job's own ceiling: at most 200 calls x 5 questions, 4 at a time.
RUN_TIME_LIMIT_SECONDS = 6 * 60 * 60
# The source the evaluator's log row and usage name.
EVAL_SOURCE = "simulate_ask"

# How a kind is named above its value in a typed question's criteria.
KIND_LABELS = {
    Kind.TRANSCRIPT: "Transcript",
    Kind.AGENT_INSTRUCTIONS: "Agent instructions",
    Kind.SCENARIO: "Scenario",
    Kind.TOOL_CALLS: "Tool calls",
    Kind.WORLD_CHANGES: "World changes",
    Kind.WORLD_STATE_END: "World state at the end",
    Kind.WORLD_STATE_START: "World state at the start",
    Kind.CHECK_RESULTS: "Check results",
    Kind.AUDIO: "Audio",
    Kind.AGENT_TRACES: "Agent traces",
}

RESULT = SimulateEvalResult.State
COUNTED_STATES = (
    RESULT.ANSWERED,
    RESULT.UNMEASURED,
    RESULT.NOT_APPLICABLE,
    RESULT.GRADING_ERROR,
    RESULT.INFRASTRUCTURE_ERROR,
    RESULT.CANCELLED,
)


class AnswerType(StrEnum):
    YES_NO = "yes_no"
    CATEGORY = "category"


# --- the definition ---------------------------------------------------------------


@dataclass(frozen=True)
class Definition:
    """What one question asks: the criteria as sent, the model, the answer's shape
    and the inputs, each a key and the kind it reads. ``ask_selection`` takes it as
    a question (its ``name``, ``kinds`` and ``decision_model``)."""

    key: str
    name: str
    criteria: str
    model: str
    answer_type: AnswerType
    labels: tuple[str, ...]
    inputs: tuple[tuple[str, Kind], ...]
    descriptions: Mapping[str, str] = field(default_factory=dict)

    @property
    def kinds(self) -> tuple[Kind, ...]:
        return tuple(dict.fromkeys(kind for _, kind in self.inputs))

    @property
    def decision_model(self) -> str:
        return self.model

    def as_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "criteria": self.criteria,
            "model": self.model,
            "answer_type": self.answer_type.value,
            "labels": list(self.labels),
            "descriptions": dict(self.descriptions),
            "inputs": [[key, kind.value] for key, kind in self.inputs],
        }


def typed_question(
    key: str,
    question: str,
    *,
    kinds: Sequence[Kind],
    answer_type: AnswerType | str = AnswerType.YES_NO,
    labels: Sequence[str] = (),
    descriptions: Mapping[str, str] | None = None,
) -> Definition:
    """A question a person typed, read the way Instant Eval reads one: Yes/No or
    categories, on its model, with each kind named in the criteria."""
    text = (question or "").strip()
    if not text or len(text) > MAX_QUESTION_LENGTH:
        raise LaunchRefused(
            "question_invalid",
            f"Write the question in 1 to {MAX_QUESTION_LENGTH} characters.",
        )
    answer_type = AnswerType(answer_type)
    if answer_type == AnswerType.YES_NO:
        labels = YES_NO_LABELS
    else:
        labels = _clean_labels(labels)
        if not 2 <= len(labels) <= MAX_CATEGORIES:
            raise LaunchRefused(
                "question_invalid", f"Use 2 to {MAX_CATEGORIES} categories."
            )
        if any(
            label.startswith("__") or len(label) > MAX_LABEL_LENGTH for label in labels
        ):
            raise LaunchRefused(
                "question_invalid",
                "Category names can't start with two underscores, and each is "
                f"at most {MAX_LABEL_LENGTH} characters.",
            )
    descriptions = {
        label: str(descriptions[label]).strip()[:MAX_DEFINITION_LENGTH]
        for label in labels
        if descriptions and str(descriptions.get(label) or "").strip()
    }
    kinds = tuple(dict.fromkeys(Kind(kind) for kind in kinds))
    if not kinds:
        raise LaunchRefused("question_invalid", "A question reads at least one kind.")
    if Kind.AUDIO in kinds:
        # The decision model takes text and JSON only (ee/jev, mapping and
        # evaluator), so a question that reads audio is refused, not sent.
        raise LaunchRefused(
            "audio_unsupported",
            "A question on the decision model can't read the call's audio.",
        )
    sections = "\n\n".join(
        f"{KIND_LABELS[kind]}:\n{{{{{kind.value}}}}}" for kind in kinds
    )
    return Definition(
        key=key,
        name=text,
        criteria=f"{text}\n\n{sections}",
        model=TYPED_QUESTION_MODEL,
        answer_type=answer_type,
        labels=tuple(labels),
        inputs=tuple((kind.value, kind) for kind in kinds),
        descriptions=descriptions,
    )


def _clean_labels(labels: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    cleaned = []
    for label in labels:
        text = label.strip() if isinstance(label, str) else ""
        if text and text.lower() not in seen:
            seen.add(text.lower())
            cleaned.append(text)
    return tuple(cleaned)


def question_version(definition: Definition, template_id: str, version: int) -> str:
    """A hash of what decides the answer: the template and its version, the model,
    the criteria as sent, the labels, the kinds it reads and the reader's version."""
    decided = {
        "template_id": template_id,
        "template_version": version,
        "model": definition.model,
        "criteria": definition.criteria,
        "answer_type": definition.answer_type.value,
        "labels": list(definition.labels),
        "descriptions": dict(definition.descriptions),
        "inputs": [[key, kind.value] for key, kind in definition.inputs],
        "reader_version": READER_VERSION,
    }
    encoded = json.dumps(decided, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


# --- the template, saved as Instant Eval saves one --------------------------------------


def _choice_scores(definition: Definition) -> dict[str, int]:
    # Yes scores 1, so the saved evaluator's pass rate is the share of yes;
    # categories carry no pass or fail, so each scores 1 (Instant Eval's rule).
    return {
        label: 1
        if definition.answer_type == AnswerType.CATEGORY or label == "Yes"
        else 0
        for label in definition.labels
    }


def _template_name(question: str, at) -> str:
    """Instant Eval's ``evaluatorName``: the question's first eight words as a
    slug, then the time, so repeated questions stay distinct."""
    import unicodedata

    folded = unicodedata.normalize("NFKD", question.lower())
    words = re.sub(r"[^a-z0-9]+", " ", folded).split()[:8]
    stamp = re.sub(r"\D", "", at.isoformat())[4:14]
    return f"{'-'.join(words) or 'instant-eval'}-{stamp}"[:255]


def save_question_template(
    definition: Definition, *, organization, workspace_id, user, at
):
    """The eval template a typed question runs on, with its first version: the
    shape Instant Eval's create call saves (an LLM judge on its model, a choice
    answer, mustache variables, a Jev mapping). Returns (template, version number)."""
    from model_hub.models.choices import OwnerChoices
    from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
    from model_hub.utils.jev_templates import validate_jev_template_save

    choice_scores = _choice_scores(definition)
    labels = list(definition.labels)
    config = {
        "output": "choices",
        "eval_type_id": "CustomPromptEvaluator",
        "required_keys": [key for key, _ in definition.inputs],
        "rule_prompt": definition.criteria,
        "system_prompt": None,
        "custom_eval": True,
        "check_internet": False,
        "few_shot_examples": [],
        "choices": labels,
        "choices_map": {
            label: "pass" if score >= 0.7 else ("neutral" if score >= 0.3 else "fail")
            for label, score in choice_scores.items()
        },
        "multi_choice": False,
        "template_format": "mustache",
        "pass_threshold": 0.5,
        "choice_scores": choice_scores,
        "error_localizer_enabled": False,
        "jev_mapping": {
            "revision": "jev-map-v1",
            "question_type": "choice",
            "pass": None,
            "choice": {
                "labels": labels,
                "descriptions": {
                    label: definition.descriptions.get(label, "") for label in labels
                },
            },
            "score": None,
            "include_messages": False,
        },
    }
    refused = validate_jev_template_save(
        config,
        model=definition.model,
        mapping_provided=True,
        choice_scores=choice_scores,
        multi_choice=False,
    )
    if refused is not None:
        raise LaunchRefused(
            "question_invalid", "This question can't be saved for the decision model."
        )
    name = _template_name(definition.name, at)
    step = 0
    while EvalTemplate.no_workspace_objects.filter(
        name=name, organization=organization, deleted=False
    ).exists():
        step += 1
        name = _template_name(definition.name, at + timedelta(seconds=step))
    template = EvalTemplate.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace_id=workspace_id,
        owner=OwnerChoices.USER.value,
        eval_type="llm",
        eval_tags=[TEMPLATE_TAG],
        config=config,
        choices=labels,
        description=definition.name,
        criteria=definition.criteria,
        multi_choice=False,
        proxy_agi=True,
        visible_ui=True,
        model=definition.model,
        output_type_normalized="deterministic",
        pass_threshold=0.5,
        choice_scores=choice_scores,
        error_localizer_enabled=False,
    )
    version = EvalTemplateVersion.objects.create_version(
        eval_template=template,
        prompt_messages=[],
        config_snapshot=config,
        criteria=definition.criteria,
        model=definition.model,
        user=user,
        organization=organization,
        workspace=_workspace(workspace_id),
    )
    return template, version.version_number


def _workspace(workspace_id):
    if workspace_id is None:
        return None
    from accounts.models.workspace import Workspace

    return Workspace.objects.filter(id=workspace_id).first()


# --- launching -----------------------------------------------------------------------


def launch_ask(
    selection: FrozenSelection,
    definitions: Sequence[Definition],
    *,
    organization,
    workspace_id,
    asked_by,
    accepted_credits: float | None,
) -> SimulateAsk:
    """Launch an ask the person has seen and accepted. The check before launch and
    its gate run again here; a cost that changed since it was shown is refused.
    Nothing is written or spent on a refusal."""
    definitions = list(definitions)
    if len({definition.key for definition in definitions}) != len(definitions):
        raise LaunchRefused("questions", "Each question needs its own key.")
    shown = check_before_launch(
        selection, definitions, organization=organization, workspace_id=workspace_id
    )
    launch_gate(
        organization=organization,
        workspace_id=workspace_id,
        questions=definitions,
        answers=shown["answers"],
    )
    if shown["credits_estimate"] != accepted_credits:
        raise LaunchRefused(
            "cost_changed",
            "The cost of this ask changed since it was shown. Check it again.",
            credits_estimate=shown["credits_estimate"],
        )
    at = timezone.now()
    with transaction.atomic():
        questions = []
        for index, definition in enumerate(definitions):
            template, version = save_question_template(
                definition,
                organization=organization,
                workspace_id=workspace_id,
                user=asked_by,
                at=at + timedelta(seconds=index),
            )
            questions.append(
                {
                    **definition.as_json(),
                    "eval_template_id": str(template.id),
                    "template_version": version,
                    "version": question_version(definition, str(template.id), version),
                }
            )
        ask = SimulateAsk.objects.create(
            organization=organization,
            workspace_id=workspace_id,
            selection=selection.as_json(),
            questions=questions,
            shown=shown,
            credits_estimate=shown["credits_estimate"],
            asked_by=asked_by,
            engine=ENGINE,
        )
        SimulateEvalResult.objects.bulk_create(
            [
                SimulateEvalResult(
                    organization=organization,
                    workspace_id=workspace_id,
                    origin=SimulateEvalResult.Origin.ASK,
                    ask=ask,
                    call_execution_id=call_id,
                    eval_config=None,
                    question_key=question["key"],
                    question_definition=question,
                    question_version=question["version"],
                    state=RESULT.PENDING,
                )
                for call_id in selection.call_ids
                for question in questions
            ]
        )
        transaction.on_commit(lambda: _queue(ask.id))
    return ask


def _queue(ask_id) -> None:
    try:
        started = run_ask.apply_async(
            args=(str(ask_id),), task_id=f"simulate-ask-{ask_id}"
        )
    except Exception:
        logger.exception("simulate_ask_queue_refused", ask_id=str(ask_id))
        now = timezone.now()
        _results().filter(ask_id=ask_id, state=RESULT.PENDING).update(
            state=RESULT.INFRASTRUCTURE_ERROR,
            cause="queue_refused",
            finished_at=now,
        )
        _asks().filter(id=ask_id).update(status=SimulateAsk.Status.FINISHED)
        return
    _asks().filter(id=ask_id).update(engine_run_id=str(getattr(started, "id", "")))


# --- the job ------------------------------------------------------------------------


@temporal_activity(
    time_limit=RUN_TIME_LIMIT_SECONDS,
    max_retries=0,
    queue="tasks_l",
)
def run_ask(ask_id: str) -> None:
    """Grade every row of one ask nobody has taken, then close the ask. A row whose
    worker died is closed as an infrastructure error and not run again."""
    _close_dead(ask_id)
    pending = list(
        _results()
        .filter(ask_id=ask_id, state=RESULT.PENDING, claimed_at__isnull=True)
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    )
    deadline = time.monotonic() + RUN_TIME_LIMIT_SECONDS - 300
    if GRADING_THREADS <= 1:
        for row_id in pending:
            _grade_when_free(str(row_id), deadline)
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=GRADING_THREADS) as pool:
            list(
                pool.map(
                    lambda row_id: _in_thread(_grade_when_free, str(row_id), deadline),
                    pending,
                )
            )
    _close_dead(ask_id)
    _finish(ask_id)


def _in_thread(work, *args):
    from django.db import close_old_connections

    close_old_connections()
    try:
        return work(*args)
    finally:
        close_old_connections()


def _grade_when_free(row_id: str, deadline: float) -> str:
    while True:
        outcome = grade_row(row_id)
        if outcome != "busy" or time.monotonic() >= deadline:
            return outcome
        time.sleep(BUSY_WAIT_SECONDS)


def _close_dead(ask_id) -> None:
    _results().filter(
        ask_id=ask_id,
        state=RESULT.PENDING,
        claimed_at__lt=timezone.now() - GRADING_GIVE_UP,
    ).update(
        state=RESULT.INFRASTRUCTURE_ERROR,
        cause="worker_died",
        finished_at=timezone.now(),
    )


def _finish(ask_id) -> None:
    if not _results().filter(ask_id=ask_id, state=RESULT.PENDING).exists():
        _asks().filter(id=ask_id, status=SimulateAsk.Status.RUNNING).update(
            status=SimulateAsk.Status.FINISHED
        )


# --- one grading ---------------------------------------------------------------------


def grade_row(row_id: str) -> str:
    """Grade one row at most once. Returns "taken" when another delivery has it or
    it is done, "busy" when the organisation is at its limit, else "done"."""
    row = _results().select_related("ask").filter(id=row_id).first()
    if row is None or row.state != RESULT.PENDING or row.claimed_at is not None:
        return "taken"
    if row.ask.status == SimulateAsk.Status.STOPPED:
        _results().filter(id=row.id, state=RESULT.PENDING, claimed_at=None).update(
            state=RESULT.CANCELLED,
            cause=row.ask.stop_cause,
            finished_at=timezone.now(),
        )
        return "done"
    claimed = _claim(row)
    if not isinstance(claimed, type(timezone.now())):
        return claimed
    try:
        fields = _grade(row)
    except EvaluationDenied as denied:
        cause = "credits" if _is_usage_limit(denied) else "denied"
        _stop(row, claimed, cause)
        return "done"
    _write(row, claimed, **fields)
    return "done"


def _claim(row):
    """Take the row, committed, before anything is called: the organisation's
    gradings in flight are counted under a lock, so the limit holds across asks."""
    now = timezone.now()
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                [f"simulate-ask-org:{row.organization_id}"],
            )
        in_flight = (
            _results()
            .filter(
                organization_id=row.organization_id,
                origin=SimulateEvalResult.Origin.ASK,
                state=RESULT.PENDING,
                claimed_at__gt=now - GRADING_GIVE_UP,
            )
            .count()
        )
        if in_flight >= MAX_GRADING_AT_ONCE_PER_ORG:
            return "busy"
        taken = (
            _results()
            .filter(id=row.id, state=RESULT.PENDING, claimed_at__isnull=True)
            .update(claimed_at=now)
        )
    return now if taken else "taken"


def _grade(row) -> dict[str, Any]:
    """The fields of the graded row. Reads the call's evidence again now, and
    stores what it read."""
    question = row.question_definition
    inputs = [(key, Kind(kind)) for key, kind in question["inputs"]]
    call = (
        CallExecution._base_manager.select_related("test_execution__run_test")
        .filter(id=row.call_execution_id)
        .first()
    )
    if call is None:
        return {"state": RESULT.UNMEASURED, "cause": "calls_gone"}
    try:
        read = read_call_evidence(
            call,
            [kind for _, kind in inputs],
            organization_id=row.organization_id,
            workspace_id=row.workspace_id,
        )
    except CallNotInWorkspace:
        return {"state": RESULT.UNMEASURED, "cause": "not_found"}
    except CallDeleted:
        return {"state": RESULT.UNMEASURED, "cause": "calls_gone"}
    evidence = {
        kind.value: {
            "state": item.state.value,
            "cause": item.cause.value if item.cause else None,
            "source": item.source,
            "digest": item.digest,
            "cut": item.cut,
            "marks": list(item.marks),
            "note": item.note,
        }
        for kind, item in read.items()
    }
    for kind, item in read.items():
        if item.state == State.NOT_CAPTURED:
            return {
                "state": RESULT.UNMEASURED,
                "cause": item.cause.value if item.cause else "not_captured",
                "evidence": evidence,
            }
    for kind, item in read.items():
        if item.state == State.NOT_APPLICABLE:
            return {
                "state": RESULT.NOT_APPLICABLE,
                "cause": kind.value,
                "evidence": evidence,
            }
    values = {key: _as_text(read[kind].content) for key, kind in inputs}
    from model_hub.models.evals_metric import EvalTemplate

    template = EvalTemplate.no_workspace_objects.get(id=question["eval_template_id"])
    if _too_large(question["model"], template, values):
        return {"state": RESULT.UNMEASURED, "cause": "too_large", "evidence": evidence}
    organization = row.ask.organization
    try:
        result = run_eval_func(
            {"run_config": {"model": question["model"]}},
            dict(values),
            template,
            organization,
            model=question["model"],
            workspace=row.ask.workspace,
            source=EVAL_SOURCE,
            input_data_types={key: "text" for key in values},
        )
    except EvaluationDenied:
        raise
    except Exception as failed:
        return {**_failure(failed), "evidence": evidence}
    if not isinstance(result, dict):
        # A string answer is a refusal of the call itself (usage, rate).
        return {
            "state": RESULT.GRADING_ERROR,
            "cause": "evaluator_refused",
            "reason": "The evaluator refused this grading.",
            "evidence": evidence,
        }
    # The shared formatter's choice answer: {"score": ..., "choice": ...}.
    output = result.get("output")
    output = output if isinstance(output, dict) else {}
    label = output.get("choice")
    score = output.get("score")
    if not isinstance(label, str) or label not in template.choice_scores:
        return {
            "state": RESULT.GRADING_ERROR,
            "cause": "answer_unreadable",
            "reason": _GRADING_FAILED,
            "evaluator_log_id": result.get("log_id"),
            "evidence": evidence,
        }
    return {
        "state": RESULT.ANSWERED,
        "label": label,
        "score": float(score) if isinstance(score, (int, float)) else None,
        "reason": result.get("reason"),
        "confidence": _confidence(result.get("metadata")),
        "evaluator_log_id": result.get("log_id"),
        "evidence": evidence,
    }


def _as_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


def _confidence(metadata: Any) -> float | None:
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            return None
    if not isinstance(metadata, dict):
        return None
    for key in ("jev", "decision"):
        value = (metadata.get(key) or {}).get("confidence")
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _too_large(model: str, template, values: Mapping[str, str]) -> bool:
    """Whether the request the decision model's evaluator would build is over its
    limits. Built with that evaluator's own functions and limits, so nothing here
    copies a number; over the limit, it is never sent."""
    try:
        from jinja2 import StrictUndefined
        from jinja2.sandbox import SandboxedEnvironment

        from ee.evals.llm.custom_prompt_evaluator.rendering import render_prompt
        from ee.jev.mapping import (
            JevMappingError,
            build_question,
            build_state,
            check_input_size,
            validate_jev_mapping,
        )
        from evaluations.decision_models import get_decision_model, is_decision_model
        from tfc.ee_gates import is_jev_model
    except ImportError:
        return False
    if not is_decision_model(model):
        return False
    config = {**(template.config or {}), "model": model}
    mapping = validate_jev_mapping(
        None,
        output=config.get("output"),
        choice_scores=template.choice_scores,
        multi_choice=False,
        config=config,
    )
    environment = SandboxedEnvironment(undefined=StrictUndefined)
    rendered = [
        render_prompt(
            config["rule_prompt"],
            dict(values),
            config.get("template_format", "mustache"),
            environment,
        )[0],
        "The state contains the input variables: " + ", ".join(values) + ".",
    ]
    question_id, question = build_question(mapping, rendered)
    state = build_state(dict(values))
    try:
        check_input_size(
            {"model": model, "state": state, "questions": {question_id: question}}
        )
    except JevMappingError:
        return True
    spec = get_decision_model(model)
    if spec is not None and not is_jev_model(model):
        state_text = (
            state
            if isinstance(state, str)
            else json.dumps(state, ensure_ascii=False, allow_nan=False)
        )
        return len(state_text.encode("utf-8")) > spec.max_state_bytes
    return False


# An evaluator failure, by its code. Only the code and the evaluator's own
# user-facing words are kept, never an exception's text.
_UNMEASURED_CODES = {
    "JEV_INPUT_TOO_LARGE": "too_large",
    "DECISION_INPUT_TOO_LARGE": "too_large",
    "JEV_MISSING_VARIABLE": "input_missing",
}
_GRADING_FAILED = "The evaluator couldn't finish this grading."


def _failure(failed: Exception) -> dict[str, Any]:
    described = None
    for describe in _describers():
        described = describe(failed)
        if described:
            break
    if not described:
        logger.warning("simulate_ask_grading_failed", error_type=type(failed).__name__)
        return {
            "state": RESULT.GRADING_ERROR,
            "cause": "evaluator_failed",
            "reason": _GRADING_FAILED,
        }
    code = described.get("code") or "evaluator_failed"
    if code in _UNMEASURED_CODES:
        return {"state": RESULT.UNMEASURED, "cause": _UNMEASURED_CODES[code]}
    return {
        "state": RESULT.GRADING_ERROR,
        "cause": code,
        "reason": described.get("message") or _GRADING_FAILED,
    }


def _describers():
    found = []
    try:
        from ee.decisions.errors import describe as describe_decision

        found.append(describe_decision)
    except ImportError:
        pass
    try:
        from ee.jev.errors import describe as describe_jev

        found.append(describe_jev)
    except ImportError:
        pass
    return found


def _is_usage_limit(denied: EvaluationDenied) -> bool:
    try:
        from ee.usage.exceptions import UsageLimitExceeded
    except ImportError:
        return False
    return isinstance(denied, UsageLimitExceeded)


def _write(row, claimed, **fields) -> None:
    written = (
        _results()
        .filter(id=row.id, state=RESULT.PENDING, claimed_at=claimed)
        .update(**fields, finished_at=timezone.now())
    )
    if not written:
        # Closed as a dead worker's row meanwhile; that row stands.
        logger.warning("simulate_ask_result_already_closed", row_id=str(row.id))


def _stop(row, claimed, cause: str) -> None:
    """A denial or credits that ran out stop the ask: this grading and every one
    not yet run are cancelled with the cause. Nothing was charged for them."""
    now = timezone.now()
    with transaction.atomic():
        _asks().filter(id=row.ask_id).update(
            status=SimulateAsk.Status.STOPPED, stop_cause=cause
        )
        _results().filter(id=row.id, state=RESULT.PENDING, claimed_at=claimed).update(
            state=RESULT.CANCELLED, cause=cause, finished_at=now
        )
        _results().filter(
            ask_id=row.ask_id, state=RESULT.PENDING, claimed_at__isnull=True
        ).update(state=RESULT.CANCELLED, cause=cause, finished_at=now)


# --- reading -----------------------------------------------------------------------


def result_counts(ask: SimulateAsk) -> dict[str, dict[str, Any]]:
    """For each question: requested, the six counts, pending, and the rate with its
    denominator (answers only; errors and unmeasured are never in it)."""
    rows = _results().filter(ask=ask).values_list("question_key", "state", "label")
    by_question: dict[str, Counter] = {}
    labels: dict[str, Counter] = {}
    for key, state, label in rows:
        by_question.setdefault(key, Counter())[state] += 1
        if state == RESULT.ANSWERED and label is not None:
            labels.setdefault(key, Counter())[label] += 1
    requested = len(ask.selection.get("call_ids") or [])
    counts = {}
    for question in ask.questions:
        key = question["key"]
        seen = by_question.get(key, Counter())
        counts[key] = {
            "requested": requested,
            **{state.value: seen.get(state.value, 0) for state in COUNTED_STATES},
            "pending": seen.get(RESULT.PENDING.value, 0),
            "rate": {
                "answered": seen.get(RESULT.ANSWERED.value, 0),
                "labels": dict(labels.get(key, Counter())),
            },
        }
    return counts


def _results():
    return SimulateEvalResult._base_manager.all()


def _asks():
    return SimulateAsk._base_manager.all()
