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
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

import structlog
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from model_hub.views.utils.evals import run_eval_func
from simulate.models import CallExecution, SimulateAsk, SimulateEvalResult
from simulate.services.ask_selection import (
    FrozenSelection,
    LaunchRefused,
    Question,
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
TEMPLATE_MARKS = ("{{", "}}", "{%", "%}", "{#", "#}")
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
# The wait doubles while the organisation stays at its limit, up to this.
MAX_BUSY_WAIT_SECONDS = 30
# The job's own ceiling: at most 200 calls x 5 questions, 4 at a time.
RUN_TIME_LIMIT_SECONDS = 6 * 60 * 60
# After this long since a launch, no job is still working on its ask.
SETTLE_AFTER = timedelta(seconds=RUN_TIME_LIMIT_SECONDS) + GRADING_GIVE_UP
SETTLE_MARGIN = timedelta(minutes=5)
# The source the evaluator's log row and usage name.
EVAL_SOURCE = "simulate_ask"
ROW_ID_KEY = "simulate_ask_result_id"

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
    if any(mark in text for mark in TEMPLATE_MARKS):
        # The question is the start of the template's instructions, so its own
        # braces would be read as variables or template code.
        raise LaunchRefused(
            "question_invalid",
            "A question can't contain {{ }}, {% %} or {# #}.",
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


# Every template field the library's update view can change in place, without a new
# version, that decides how a row is graded. Name, description and tags do not.
_GRADED_TEMPLATE_FIELDS = (
    "config",
    "model",
    "choices",
    "choice_scores",
    "multi_choice",
    "eval_type",
    "pass_threshold",
    "output_type_normalized",
    "criteria",
)


def template_digest(template) -> str:
    """A hash of what the template grades with, as it is stored now."""
    graded = {name: getattr(template, name) for name in _GRADED_TEMPLATE_FIELDS}
    config = graded["config"]
    if isinstance(config, dict) and isinstance(config.get("required_keys"), list):
        # The update view rebuilds the inputs from a set, in an order that differs
        # between processes: the same inputs in another order are the same question.
        graded["config"] = {
            **config,
            "required_keys": sorted(config["required_keys"], key=str),
        }
    encoded = json.dumps(graded, sort_keys=True, separators=(",", ":"), default=str)
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
    while _name_taken(EvalTemplate, name, organization):
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


def _name_taken(model, name, organization) -> bool:
    """The create-v2 view's uniqueness: the organisation's own templates and the
    system's."""
    from model_hub.models.choices import OwnerChoices

    return bool(
        model.no_workspace_objects.filter(
            name=name, organization=organization, deleted=False
        ).exists()
        or model.no_workspace_objects.filter(
            name=name, owner=OwnerChoices.SYSTEM.value, deleted=False
        ).exists()
    )


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
    longest = (
        getattr(SimulateEvalResult._meta.get_field("question_key"), "max_length", None)
        or 255
    )
    if any(len(definition.key) > longest for definition in definitions):
        raise LaunchRefused(
            "questions", f"A question's key is at most {longest} characters."
        )
    checked = [
        Question(definition.name, definition.kinds, definition.decision_model)
        for definition in definitions
    ]
    shown = check_before_launch(
        selection, checked, organization=organization, workspace_id=workspace_id
    )
    launch_gate(
        organization=organization,
        workspace_id=workspace_id,
        questions=checked,
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
            # Read back as stored, so a grading compares like with like.
            template.refresh_from_db()
            questions.append(
                {
                    **definition.as_json(),
                    "eval_template_id": str(template.id),
                    "template_version": version,
                    "template_digest": template_digest(template),
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
    _queue_settle(ask_id)
    try:
        started = run_ask.apply_async(
            args=(str(ask_id),), task_id=f"simulate-ask-{ask_id}"
        )
    except Exception:
        logger.exception("simulate_ask_queue_refused", ask_id=str(ask_id))
        # A start whose response was lost may still have started the job: a row it
        # claimed is left to it, and the later settle closes what it leaves.
        _results().filter(
            ask_id=ask_id, state=RESULT.PENDING, claimed_at__isnull=True
        ).update(
            state=RESULT.INFRASTRUCTURE_ERROR,
            cause="queue_refused",
            finished_at=timezone.now(),
        )
        _finish(ask_id)
        return
    _asks().filter(id=ask_id).update(engine_run_id=str(getattr(started, "id", "")))


def _queue_settle(ask_id, *, follow_up: bool = False) -> None:
    """Settle the ask once no delivery of its job can still be working: after the
    job's time limit and the give-up time for its last claims. A follow-up settles
    claims that were still live when the job or a settle ended, once they are past
    the give-up time."""
    if follow_up:
        task_id = f"simulate-ask-settle-{ask_id}-{uuid.uuid4().hex[:12]}"
        delay = GRADING_GIVE_UP + SETTLE_MARGIN
    else:
        task_id = f"simulate-ask-settle-{ask_id}"
        delay = SETTLE_AFTER + SETTLE_MARGIN
    try:
        settle_ask_later.apply_async(
            args=(str(ask_id),), task_id=task_id, start_delay=delay
        )
    except Exception:
        # A reader settles the ask too; nothing is lost but the timing.
        logger.exception("simulate_ask_settle_queue_refused", ask_id=str(ask_id))


# --- the job ------------------------------------------------------------------------


@temporal_activity(
    time_limit=RUN_TIME_LIMIT_SECONDS,
    # A worker restart (a deploy) delivers the job again: the claim makes that
    # safe, and the new delivery grades only the rows nobody took.
    max_retries=2,
    queue="tasks_l",
)
def run_ask(ask_id: str) -> None:
    """Grade every row of one ask nobody has taken, then close the ask. A row whose
    worker died is closed as an infrastructure error and not run again."""
    try:
        pending = list(
            _results()
            .filter(ask_id=ask_id, state=RESULT.PENDING, claimed_at__isnull=True)
            .order_by("created_at", "id")
            .values_list("id", flat=True)
        )
        deadline = time.monotonic() + RUN_TIME_LIMIT_SECONDS - 300
        if GRADING_THREADS <= 1:
            for row_id in pending:
                _grade_one(str(row_id), deadline)
        else:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=GRADING_THREADS) as pool:
                list(
                    pool.map(
                        lambda row_id: _in_thread(_grade_one, str(row_id), deadline),
                        pending,
                    )
                )
    finally:
        # Rows still waiting for a free place when the job's time ran out are not
        # run by anyone; claimed rows of another delivery are left to it.
        _results().filter(
            ask_id=ask_id, state=RESULT.PENDING, claimed_at__isnull=True
        ).update(
            state=RESULT.INFRASTRUCTURE_ERROR,
            cause="not_run",
            finished_at=timezone.now(),
        )
        settle_ask(ask_id, follow_up=True)


def settle_ask(ask_id, *, follow_up: bool = False) -> None:
    """Close what no worker will finish, then the ask. A claim older than
    ``GRADING_GIVE_UP`` lost its worker; once the job's own time limit has passed
    since the launch, a row nobody claimed will not be run either. The job calls
    this when it ends; a reader calls it first, for an ask whose job died.

    With ``follow_up`` (the job and the later settle, never a reader), a claim
    still too young to call dead gets one more settle after the give-up time.
    Claims are only taken on unclaimed rows, which the settle after the job's time
    limit closes, so these follow-ups end."""
    _close_dead(ask_id)
    _results().filter(
        ask_id=ask_id,
        state=RESULT.PENDING,
        claimed_at__isnull=True,
        created_at__lt=timezone.now() - SETTLE_AFTER,
    ).update(
        state=RESULT.INFRASTRUCTURE_ERROR,
        cause="job_ended",
        finished_at=timezone.now(),
    )
    _finish(ask_id)
    if (
        follow_up
        and _results()
        .filter(ask_id=ask_id, state=RESULT.PENDING, claimed_at__isnull=False)
        .exists()
    ):
        _queue_settle(ask_id, follow_up=True)


@temporal_activity(time_limit=600, max_retries=3, queue="tasks_s")
def settle_ask_later(ask_id: str) -> None:
    """``settle_ask``, queued at launch to run after every delivery of the job is
    over, so an ask whose job died is closed without waiting for a reader."""
    settle_ask(ask_id, follow_up=True)


def _in_thread(work, *args):
    from django.db import close_old_connections, connections

    close_old_connections()
    try:
        return work(*args)
    finally:
        # The pool's threads end with the job: release their connections now.
        connections.close_all()


def _grade_one(row_id: str, deadline: float) -> str:
    """One row, isolated: a failure here is logged and leaves the others running;
    the job's close and settle still run."""
    try:
        return _grade_when_free(row_id, deadline)
    except Exception:  # noqa: BLE001 - logged; one row must not end the job
        logger.exception("simulate_ask_row_failed", row_id=row_id)
        return "failed"


def _grade_when_free(row_id: str, deadline: float) -> str:
    wait = BUSY_WAIT_SECONDS
    while True:
        if time.monotonic() >= deadline:
            # Past the job's time, nothing more is graded or charged.
            return "not_run"
        outcome = grade_row(row_id)
        if outcome != "busy":
            return outcome
        time.sleep(wait)
        wait = min(wait * 2, MAX_BUSY_WAIT_SECONDS)


def _close_dead(ask_id) -> None:
    """A claim older than the give-up time lost its worker. Its row is closed, and
    names the evaluator's log row when the evaluator had been called."""
    dead = _results().filter(
        ask_id=ask_id,
        state=RESULT.PENDING,
        claimed_at__lt=timezone.now() - GRADING_GIVE_UP,
    )
    for row_id, organization_id, claimed, question in dead.values_list(
        "id", "organization_id", "claimed_at", "question_definition"
    ):
        _results().filter(id=row_id, state=RESULT.PENDING, claimed_at=claimed).update(
            state=RESULT.INFRASTRUCTURE_ERROR,
            cause="worker_died",
            evaluator_log_id=_logged_call(
                row_id, organization_id, _template_id(question)
            ),
            finished_at=timezone.now(),
        )


def _template_id(question) -> str | None:
    if isinstance(question, Mapping) and question.get("eval_template_id"):
        return str(question["eval_template_id"])
    return None


def _logged_call(row_id, organization_id, template_id):
    """The evaluator's log row for this grading, if the evaluator was called: the
    grading passes the row's id in the config the log row keeps. The log row names
    the template as its source, which keeps the lookup on the org's own index; a
    row kept without its question is looked up without it."""
    from django.apps import apps

    try:
        log_model = apps.get_model("usage", "APICallLog")
    except LookupError:
        return None
    logs = log_model.objects.filter(organization_id=organization_id)
    if template_id:
        logs = logs.filter(source_id=template_id)
    # The log row keeps its config as JSON text; the row id is a UUID.
    return (
        logs.filter(source=EVAL_SOURCE, config__icontains=str(row_id))
        .values_list("log_id", flat=True)
        .first()
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
    if isinstance(claimed, str):
        return claimed
    try:
        fields = _grade(row)
    except EvaluationDenied as denied:
        _stop(row, claimed, "credits" if _is_usage_limit(denied) else "denied")
        return "done"
    except _CreditsRefused:
        _stop(row, claimed, "credits")
        return "done"
    except Exception as failed:  # noqa: BLE001 - logged; the row must close
        # Anything else after the claim still closes the row, so no claim is
        # left holding one of the organisation's places.
        logger.warning("simulate_ask_step_failed", error_type=type(failed).__name__)
        fields = {
            "state": RESULT.GRADING_ERROR,
            "cause": "step_failed",
            "reason": _GRADING_FAILED,
        }
    _write(row, claimed, **fields)
    return "done"


class _CreditsRefused(Exception):
    """The evaluator's usage log refused the call for credits or a resource limit."""


def _refused_for_credits(failed: Exception) -> bool:
    # run_eval_func raises ValueError("API call not allowed : ", status) when its
    # usage log row is not created as processing.
    from tfc.constants.api_calls import APICallStatusChoices

    args: tuple[Any, ...] = getattr(failed, "args", ())
    return (
        isinstance(failed, ValueError)
        and len(args) == 2
        and isinstance(args[0], str)
        and args[0].startswith("API call not allowed")
        and args[1]
        in (
            APICallStatusChoices.INSUFFICIENT_CREDITS.value,
            APICallStatusChoices.RESOURCE_LIMIT.value,
        )
    )


def _claim(row) -> datetime | str:
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
    for item in read.values():
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

    template = EvalTemplate.no_workspace_objects.filter(
        id=question["eval_template_id"], deleted=False
    ).first()
    if template is None or not _as_launched(template, question):
        # The template is in the org's library, so it can be edited or deleted
        # while the ask runs. The question is graded only as it was launched.
        return {
            "state": RESULT.GRADING_ERROR,
            "cause": "question_changed",
            "reason": "This question's evaluator was changed or deleted after the "
            "ask started, so it wasn't graded.",
            "evidence": evidence,
        }
    if _too_large(question["model"], template, values):
        return {"state": RESULT.UNMEASURED, "cause": "too_large", "evidence": evidence}
    organization = row.ask.organization
    try:
        result = run_eval_func(
            # The row's id travels into the evaluator's log row, so a row closed
            # as a dead worker's can still name the call that was charged.
            {"run_config": {"model": question["model"]}, ROW_ID_KEY: str(row.id)},
            dict(values),
            template,
            organization,
            model=question["model"],
            workspace=row.ask.workspace,
            source=EVAL_SOURCE,
            input_data_types=dict.fromkeys(values, "text"),
        )
    except EvaluationDenied:
        raise
    except Exception as failed:  # noqa: BLE001 - every failure is a grading_error
        if _refused_for_credits(failed):
            raise _CreditsRefused() from None
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
    if not isinstance(label, str) or label not in (template.choice_scores or {}):
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


def _as_launched(template, question: Mapping[str, Any]) -> bool:
    """Whether the template still grades what the launch saved: every field the
    library can change in place, and its current version."""
    from model_hub.models.evals_metric import EvalTemplateVersion

    current = EvalTemplateVersion.objects.get_default(template)
    return (
        template_digest(template) == question.get("template_digest")
        and current is not None
        and current.version_number == question["template_version"]
    )


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
        # Closed as a dead worker's row meanwhile; that row stands, but it names
        # the evaluator's log row, since this grading was charged.
        logger.warning("simulate_ask_result_already_closed", row_id=str(row.id))
        # A failed grading carries no log id, though the evaluator may have logged
        # (and charged) the call before it failed: look it up.
        log_id = fields.get("evaluator_log_id") or _logged_call(
            row.id, row.organization_id, _template_id(row.question_definition)
        )
        if log_id:
            _results().filter(id=row.id, evaluator_log_id__isnull=True).update(
                evaluator_log_id=log_id
            )


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
    """For each question: requested, the six counts, pending, the calls gone since
    (a call, run or run test deleted: its rows are left out), and the rate with its
    denominator (answers only; errors and unmeasured are never in it)."""
    rows = _results().filter(ask=ask)
    deleted = (
        Q(call_execution__deleted=True)
        | Q(call_execution__test_execution__deleted=True)
        | Q(call_execution__test_execution__run_test__deleted=True)
    )
    gone = Counter(rows.filter(deleted).values_list("question_key", flat=True))
    by_question: dict[str, Counter] = {}
    labels: dict[str, Counter] = {}
    for key, state, label in rows.exclude(deleted).values_list(
        "question_key", "state", "label"
    ):
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
            "calls_gone": gone.get(key, 0),
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
