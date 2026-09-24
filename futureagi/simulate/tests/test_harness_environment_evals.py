"""The environment's eval endpoints, over HTTP: adding an eval by hand, and
adding one from inside a run (which also grades that run's already-finished
calls). The remove refusals live in a sibling test module and are not
asserted here.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import CallExecution, SimulateEvalConfig, TestExecution
from simulate.services.harness_evals import EVAL_RUN_CREDITS, offerable_eval_names
from simulate.services.harness_run_evals import (
    EVAL_QUEUE_STAMP_WINDOW,
    EVAL_QUEUED_KEY,
    _queued_within_window,
)
from simulate.services.hosted_harness import (
    canonical_digest,
    create_hosted_job,
    register_attempt,
)

from .test_hosted_harness_channels import _headers, _payload

ATTEMPTS = "/simulate/api/harness/attempts"
ENVIRONMENTS = "/simulate/api/harness-environments"


def _template(name, required_keys, *, tags=("Conversation",), **extra):
    return EvalTemplate.objects.create(
        name=name,
        description=f"{name} description",
        config={"required_keys": list(required_keys)},
        eval_tags=list(tags),
        eval_id=extra.pop("eval_id", 0),
        **extra,
    )


def _assert_catalog_key(name: str, *, listed: bool) -> None:
    """Fail loudly if a catalog edit changes whether ``name`` is a listed key.

    These tests hard-code names against the pinned catalog lists.
    ``offerable_eval_names()`` is the exact lookup the offer rule itself uses,
    so a catalog edit that adds or drops one of these names breaks this guard
    loudly instead of leaving the test passing for a different reason than
    its name claims.
    """
    is_listed = name in offerable_eval_names()
    assert is_listed is listed, (
        f"{name} is {'no longer' if listed else 'now'} a catalog key; "
        "update the tests that depend on that"
    )


@pytest.fixture
def environment(db, user, workspace):
    """A built environment: a hosted job whose attempt has registered scenarios.

    Registering scenarios is what creates the run test, and every evaluation
    endpoint hangs off that (views/harness_environment.py::_run_test_job).
    """
    job, _ = create_hosted_job(
        user.organization,
        _payload(),
        idempotency_key="env-evals",
        workspace=workspace,
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    response = APIClient().post(
        f"{ATTEMPTS}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Refunds",
            "modality": "voice",
            "personas": [
                {
                    "scenario_key": "refund-request",
                    "name": "Customer",
                    "situation": "Asks for a refund",
                    "outcome": "Agent follows policy",
                }
            ],
        },
        format="json",
        **_headers(capability),
    )
    assert response.status_code == 200, response.content
    job.refresh_from_db()
    return job


@pytest.fixture
def env_client(user, workspace):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _available(client, job, workspace):
    return client.get(
        f"{ENVIRONMENTS}/{job.id}/evaluations/available/",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )


def _detail(client, job, workspace):
    return client.get(
        f"{ENVIRONMENTS}/{job.id}/", HTTP_X_WORKSPACE_ID=str(workspace.id)
    )


def _add(client, job, workspace, name):
    return client.post(
        f"{ENVIRONMENTS}/{job.id}/evaluations/",
        {"name": name},
        format="json",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )


def _file_receipt(job, *, scenario_key="refund-request"):
    """Post one accepted result receipt for a registered scenario.

    ``evaluations.results`` is built only from accepted receipts
    (services/harness_environment.py::_results); a test that wants to prove it
    is unchanged needs one filed first, or "unchanged" holds trivially of an
    empty list either way. Registers a fresh attempt (superseding the one the
    ``environment`` fixture used), begins the sealed scenario set, then files
    a minimal ``skipped`` receipt — the cheapest status the serializer
    accepts, needing no call or evaluation payload.
    """
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    headers = _headers(capability)
    api = APIClient()
    begin = api.post(
        f"{ATTEMPTS}/{capability.attempt.id}/scenarios/",
        {
            "operation": "begin",
            "run_test_id": str(job.run_test_id),
            "scenario_keys": [scenario_key],
        },
        format="json",
        **headers,
    )
    assert begin.status_code == 200, begin.content
    scenario_id = str(
        job.scenario_registrations.get(scenario_key=scenario_key).scenario_id
    )
    receipt = {
        "schema_version": "futureagi.harness-result.v1",
        "job_id": str(job.id),
        "attempt_id": str(capability.attempt.id),
        "attempt_number": capability.attempt.attempt_number,
        "scenario_key": scenario_key,
        "scenario_id": scenario_id,
        "scenario_attempt": 1,
        "world_index": None,
        "status": "skipped",
        "sub_goals": [],
        "evaluations": [],
        "call": None,
        "failure": None,
    }
    receipt["digest"] = canonical_digest(receipt)
    result = api.post(
        f"{ATTEMPTS}/{capability.attempt.id}/results/",
        receipt,
        format="json",
        **headers,
    )
    assert result.status_code == 200, result.content


@pytest.mark.django_db
def test_available_sorted_and_excludes_selected(env_client, environment, workspace):
    """`available` returns one shape, one agent kind, sorted by name, every
    entry addable as it stands."""
    # Created in alphabetical (not pinned) order so EvalTemplate's default
    # `-created_at` ordering would return the reverse list, proving the
    # `.order_by("name")` sort guard below actually bites.
    for name in ("audio_quality", "conversation_coherence", "no_misselling"):
        _assert_catalog_key(name, listed=True)
        _template(name, ["conversation"], tags=("Conversation",))
    # A real catalog key whose required key no offered modality can fill, so
    # it clears the catalog and tag gates and is dropped only by the mapping
    # gate (harness_evals.py::resolve_eval_mapping). A name the catalog does
    # not list at all would be dropped earlier, at the first gate, and would
    # not exercise the mapping gate this assertion is meant to pin.
    _assert_catalog_key("conversation_hallucination", listed=True)
    _template(
        "conversation_hallucination", ["retrieved_context"], tags=("Conversation",)
    )

    response = _available(env_client, environment, workspace)
    assert response.status_code == 200, response.content
    entries = response.json()["evaluations"]
    names = [entry["name"] for entry in entries]
    assert names == ["audio_quality", "conversation_coherence", "no_misselling"]
    assert names == sorted(names)
    assert (
        "conversation_hallucination" not in names
    ), "an unfillable eval must not be offered"
    for entry in entries:
        assert entry["agent_type"] == "voice"
        assert {row["key"] for row in entry["inputs"]} == set(entry["required_keys"])
        assert set(entry) == {
            "name",
            "description",
            "source",
            "tags",
            "required_keys",
            "agent_type",
            "modality",
            "credits_per_run",
            "charges_judge_tokens",
            "inputs",
        }


@pytest.mark.django_db
def test_available_subtracts_a_row_bound_under_the_harness_own_column_name(
    env_client, environment, workspace
):
    """A row ingestion makes for a harness result column is named after that
    column, not after the template, so both names must be subtracted from
    `available` — otherwise the eval is offered a second time under a second
    id."""
    from simulate.services.alk_simulate_ingestion import (
        _get_or_create_harness_eval_config,
    )

    for name in ("no_misselling", "conversation_coherence", "audio_quality"):
        _assert_catalog_key(name, listed=True)
    template = _template("no_misselling", ["conversation"], tags=("Conversation",))
    _template("conversation_coherence", ["conversation"], tags=("Conversation",))
    other = _template("audio_quality", ["conversation"], tags=("Conversation",))

    config = _get_or_create_harness_eval_config(
        environment.run_test, template, "mis_selling_check"
    )
    assert config.mapping == {}
    assert config.name == "mis_selling_check"

    collided = _get_or_create_harness_eval_config(
        environment.run_test, other, "conversation_coherence"
    )
    assert collided.mapping == {}
    assert collided.name == "conversation_coherence"

    response = _available(env_client, environment, workspace)
    assert response.status_code == 200, response.content
    names = [entry["name"] for entry in response.json()["evaluations"]]
    assert names == []  # both offers are now bound, one under each name


@pytest.mark.django_db
def test_selected_excludes_empty_mapping_rows(env_client, environment, workspace):
    """An empty-mapping row is bound but was never selected, so it is not
    listed and does not count."""
    from simulate.services.alk_simulate_ingestion import (
        _get_or_create_harness_eval_config,
    )
    from simulate.services.harness_evals import add_selected_eval

    _assert_catalog_key("no_misselling", listed=True)
    _assert_catalog_key("booking_created", listed=False)
    chosen = _template("no_misselling", ["conversation"], tags=("Conversation",))
    column = _template("booking_created", ["conversation"], tags=("Conversation",))
    add_selected_eval(environment.run_test, chosen.name, "voice")
    _get_or_create_harness_eval_config(environment.run_test, column, "booking_created")
    assert SimulateEvalConfig.objects.filter(run_test=environment.run_test).count() == 2

    response = _detail(env_client, environment, workspace)
    assert response.status_code == 200, response.content
    body = response.json()
    selected = body["evaluations"]["selected"]
    assert [row["name"] for row in selected] == ["no_misselling"]
    assert body["overview"]["evaluations_count"] == 1
    row = selected[0]
    assert row["runnable"] is True
    assert row["id"]
    assert row["inputs"] == [
        {"key": "conversation", "source": "voice_recording", "label": "Call recording"}
    ]
    assert row["source"] == "system"
    assert row["credits_per_run"] == EVAL_RUN_CREDITS
    assert row["charges_judge_tokens"] is True


# --- Adding an eval by hand ----------------------------------------------------


@pytest.mark.django_db
def test_add_idempotent(env_client, environment, workspace):
    """Adding the same name twice returns 201 and leaves one config row; a
    name already bound under a harness result-column name is not re-bound
    under the template's own name either."""
    from simulate.services.alk_simulate_ingestion import (
        _get_or_create_harness_eval_config,
    )

    _assert_catalog_key("no_misselling", listed=True)
    template = _template("no_misselling", ["conversation"], tags=("Conversation",))

    first = _add(env_client, environment, workspace, "no_misselling")
    assert first.status_code == 201, first.content
    second = _add(env_client, environment, workspace, "no_misselling")
    assert second.status_code == 201, second.content
    assert (
        SimulateEvalConfig.objects.filter(
            run_test=environment.run_test, deleted=False
        ).count()
        == 1
    )

    # Bound under a harness column name distinct from the template's own name.
    _assert_catalog_key("audio_quality", listed=True)
    column_template = _template(
        "audio_quality", ["conversation"], tags=("Conversation",)
    )
    _get_or_create_harness_eval_config(
        environment.run_test, column_template, "call_quality_score"
    )
    by_template_name = _add(env_client, environment, workspace, "audio_quality")
    assert by_template_name.status_code == 201, by_template_name.content
    assert (
        SimulateEvalConfig.objects.filter(
            run_test=environment.run_test,
            deleted=False,
            eval_template=column_template,
        ).count()
        == 1
    ), "adding by the template's own name must not create a second row"


@pytest.mark.django_db
def test_add_refusals(env_client, environment, workspace):
    """The listed/tag gate, the unmappable-input gate, and an unknown body
    field.

    The 256-character `too_long` case below 400s through
    `HarnessEnvironmentSelectedEvalSerializer.name`'s `max_length=255`, not
    through `add_selected_eval`'s own 255-character gate — that gate's own
    coverage is `test_add_refuses_a_name_too_long_for_the_bound_row` below,
    which calls the service directly.
    """
    _assert_catalog_key("toxicity", listed=True)
    # chat-only tags; this environment's modality is voice
    _template("toxicity", ["output"], tags=("Chatbot behaviors",))
    _template(
        "conversation_hallucination", ["retrieved_context"], tags=("Conversation",)
    )  # no source in either table resolves `retrieved_context`

    wrong_kind = _add(env_client, environment, workspace, "toxicity")
    assert wrong_kind.status_code == 400, wrong_kind.content
    assert wrong_kind.json()["detail"] == (
        "toxicity: not an eval this environment can be graded by"
    )

    unmappable = _add(env_client, environment, workspace, "conversation_hallucination")
    assert unmappable.status_code == 400, unmappable.content
    assert unmappable.json()["detail"] == (
        "conversation_hallucination: needs retrieved_context, "
        "which a voice run does not produce"
    )

    too_long = _add(env_client, environment, workspace, "a" * 256)
    assert too_long.status_code == 400, too_long.content

    unknown_field = env_client.post(
        f"{ENVIRONMENTS}/{environment.id}/evaluations/",
        {"name": "no_misselling", "unexpected": "field"},
        format="json",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    assert unknown_field.status_code == 400, unknown_field.content


@pytest.mark.django_db
def test_add_refuses_a_name_too_long_for_the_bound_row(environment, user, workspace):
    """`add_selected_eval`'s own 255-character gate, exercised with no HTTP
    serializer in the way to answer for it — the serializer would refuse the
    request before the service is ever called, which is the gap this test
    closes. A template is planted under the over-long name first, so the
    test cannot pass merely because the name was never found.
    """
    from simulate.services.harness_evals import (
        _MOST_NAME_CHARACTERS,
        EvalSelectionRefused,
        add_selected_eval,
    )

    over = "b" * (_MOST_NAME_CHARACTERS + 1)
    _template(
        over,
        ["conversation"],
        tags=("Agents",),
        owner="user",
        organization=user.organization,
        workspace=workspace,
    )
    with pytest.raises(EvalSelectionRefused):
        add_selected_eval(environment.run_test, over, "voice")


@pytest.mark.django_db
def test_add_cap_counts_mapping_rows_only(env_client, environment, workspace):
    """The cap of 8 counts mapping-bearing rows only: five empty-mapping
    harness-column rows are bound first and must not eat into the cap."""
    from simulate.services.alk_simulate_ingestion import (
        _get_or_create_harness_eval_config,
    )
    from simulate.services.harness_evals import MOST_SELECTED_EVALS

    cap_fillers = [
        "advice_authority_boundary",
        "claim_intake_accuracy",
        "conversation_coherence",
        "conversation_hallucination",
        "conversation_resolution",
        "customer_agent_clarification_seeking",
        "customer_agent_context_retention",
        "customer_agent_conversation_quality",
    ]
    assert len(cap_fillers) == MOST_SELECTED_EVALS, "the cap must be filled exactly"

    for index in range(5):
        column_template = _template(
            f"harness_column_eval_{index}", ["conversation"], tags=("Conversation",)
        )
        _get_or_create_harness_eval_config(
            environment.run_test, column_template, f"result_column_{index}"
        )
    assert SimulateEvalConfig.objects.filter(run_test=environment.run_test).count() == 5

    for name in cap_fillers:
        _assert_catalog_key(name, listed=True)
        _template(name, ["conversation"], tags=("Conversation",))
        response = _add(env_client, environment, workspace, name)
        assert response.status_code == 201, (name, response.content)

    assert (
        SimulateEvalConfig.objects.filter(
            run_test=environment.run_test, deleted=False
        ).count()
        == 5 + MOST_SELECTED_EVALS
    )

    _assert_catalog_key("no_misselling", listed=True)
    _template("no_misselling", ["conversation"], tags=("Conversation",))
    over = _add(env_client, environment, workspace, "no_misselling")
    assert over.status_code == 409, over.content
    assert over.json()["detail"] == (
        f"no_misselling: an environment runs at most {MOST_SELECTED_EVALS} "
        "evals; remove one before adding another"
    )


@pytest.mark.django_db
def test_add_never_grades_finished_calls(env_client, environment, workspace):
    """Adding an eval from here never grades a call that already completed —
    the add endpoint binds a config and touches nothing else."""
    from unittest.mock import patch

    _assert_catalog_key("no_misselling", listed=True)
    _template("no_misselling", ["conversation"], tags=("Conversation",))
    _file_receipt(environment)

    with patch(
        "simulate.services.test_executor._run_simulate_evaluations_task.apply_async"
    ) as dispatch:
        response = _add(env_client, environment, workspace, "no_misselling")
        assert response.status_code == 201, response.content
        dispatch.assert_not_called()


@pytest.mark.django_db
def test_available_refusals(env_client, user, workspace):
    """The check is 'no run test yet', not a build state."""
    job, _ = create_hosted_job(
        user.organization,
        _payload(),
        idempotency_key="env-evals-unbuilt",
        workspace=workspace,
    )
    response = _available(env_client, job, workspace)
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Environment has no evaluations until it finishes building"
    )


@pytest.mark.django_db
def test_available_refusals_404(env_client, workspace):
    """An environment the caller cannot see is a 404, and a hand-edited id
    that is not a UUID must be a miss, not a 500."""
    import uuid

    missing = env_client.get(
        f"{ENVIRONMENTS}/{uuid.uuid4()}/evaluations/available/",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    nonsense = env_client.get(
        f"{ENVIRONMENTS}/not-a-uuid/evaluations/available/",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Environment not found"
    assert nonsense.status_code == 404
    assert nonsense.json()["detail"] == "Environment not found"


@pytest.mark.django_db
def test_results_unchanged(env_client, environment, workspace):
    """`evaluations.results[]` is the receipt-based list; adding a platform
    eval leaves it byte-identical."""
    from simulate.services.harness_evals import add_selected_eval

    _assert_catalog_key("no_misselling", listed=True)
    _template("no_misselling", ["conversation"], tags=("Conversation",))
    _file_receipt(environment)

    before_response = _detail(env_client, environment, workspace)
    assert before_response.status_code == 200, before_response.content
    before = json.dumps(
        before_response.json()["evaluations"]["results"], sort_keys=True
    )
    assert before != "[]", "the filed receipt must have landed, or this proves nothing"

    add_selected_eval(environment.run_test, "no_misselling", "voice")

    after_response = _detail(env_client, environment, workspace)
    assert after_response.status_code == 200, after_response.content
    body = after_response.json()
    assert json.dumps(body["evaluations"]["results"], sort_keys=True) == before
    assert [row["name"] for row in body["evaluations"]["selected"]] == [
        "no_misselling"
    ], "the add must have landed, or the comparison above proves nothing"


# --- Add an eval from inside a run ------------------------------------------
#
# Every test that posts to this endpoint must take the `dispatch` fixture, or
# a real `apply_async` escapes into Temporal.
#
# Every test that posts and then expects a dispatch outcome -- a stamp being
# queued, `dispatch` being called, or `dispatch.assert_not_called()` -- must
# run that POST inside `django_capture_on_commit_callbacks(execute=True)`.
# Every grading job this endpoint queues is scheduled with
# `transaction.on_commit`, which Django only runs once the outermost
# transaction commits; pytest-django's `db` fixture wraps each test in a
# transaction that is rolled back, never committed, so a plain
# `@pytest.mark.django_db` HTTP test here would see zero dispatch calls
# regardless of what the endpoint actually did.
# `test_nothing_is_dispatched_before_the_stamp_is_committed` below uses
# `execute=False` instead, to prove the ordering itself.


def _run_add(client, job, execution, workspace, name):
    return client.post(
        f"{ENVIRONMENTS}/{job.id}/runs/{execution.id}/evaluations/",
        {"name": name},
        format="json",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )


@pytest.fixture
def dispatch():
    """Spy on the one grading job this endpoint can start.

    Patches the attribute on the task object itself, so the service's
    function-local import sees the patch.
    """
    from unittest.mock import patch

    with patch(
        "simulate.services.test_executor._run_simulate_evaluations_task.apply_async"
    ) as spy:
        yield spy


@pytest.fixture
def finished_run(environment):
    """One finished run of this environment, with no calls yet.

    Built directly rather than through the attempt's `scenarios/` endpoint,
    because the point of these tests is to put each call in exactly one of
    the states this endpoint distinguishes, which a real `begin` cannot do.
    """
    run_test = environment.run_test
    scenario = run_test.scenarios.first()
    assert scenario is not None, "the provision fixture must leave a scenario"
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        scenario_ids=[str(scenario.id)],
    )


def _call(execution, *, status="completed", metadata=None, eval_outputs=None):
    """One call on a run, in a state this endpoint has an opinion about."""
    return CallExecution.objects.create(
        test_execution=execution,
        scenario=execution.run_test.scenarios.first(),
        status=status,
        call_metadata=metadata if metadata is not None else {},
        eval_outputs=eval_outputs if eval_outputs is not None else {},
    )


def _graded():
    """The call-level metadata of a call whose evaluations have finished."""
    return {"eval_started": True, "eval_completed": True}


def _verdict(name="no_misselling"):
    """One stored verdict, in the shape the judge path writes."""
    return {
        "name": name,
        "output": "Passed",
        "output_type": "Pass/Fail",
        "reason": "the agent stayed inside its mandate",
        "status": "completed",
    }


def _bound_config(job, name="no_misselling"):
    return SimulateEvalConfig.objects.get(run_test_id=job.run_test_id, name=name)


@pytest.mark.django_db
def test_run_add_counts(
    env_client,
    environment,
    finished_run,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
):
    """The five counts partition the run's finished calls; a failed call is
    not a finished call and is not counted."""
    template = _template("no_misselling", ["conversation"], tags=("Conversation",))
    already_graded = _call(finished_run, metadata=_graded())
    pending = _call(finished_run, metadata={"eval_started": True})
    in_flight = _call(finished_run, metadata=_graded())
    eligible = _call(finished_run, metadata=_graded())
    _call(finished_run, status="failed", metadata=_graded())

    # The bound config does not exist until the add runs, so the two states
    # that name it are planted after a first add that binds nothing else.
    with django_capture_on_commit_callbacks(execute=True):
        first = _run_add(
            env_client, environment, finished_run, workspace, template.name
        )
    assert first.status_code == 202, first.content
    config = _bound_config(environment)
    dispatch.reset_mock()

    already_graded.eval_outputs = {str(config.id): _verdict()}
    already_graded.save(update_fields=["eval_outputs"])
    for call_execution in (in_flight, eligible, pending):
        call_execution.refresh_from_db()
    # `eligible` was stamped by the first add; clear it so it is eligible again,
    # and leave `in_flight`'s stamp in place.
    eligible.call_metadata = _graded()
    eligible.save(update_fields=["call_metadata"])

    with django_capture_on_commit_callbacks(execute=True):
        response = _run_add(
            env_client, environment, finished_run, workspace, template.name
        )

    assert response.status_code == 202, response.content
    body = response.json()
    assert body == {
        "queued": 1,
        "skipped_existing": 1,
        "skipped_in_flight": 1,
        "skipped_pending": 1,
        "completed_calls": 4,
    }
    assert (
        sum(
            body[k]
            for k in (
                "queued",
                "skipped_existing",
                "skipped_pending",
                "skipped_in_flight",
            )
        )
        == body["completed_calls"]
    ), "P19: the four counts partition completed_calls exactly, no shortfall"
    assert dispatch.call_count == 1
    assert dispatch.call_args.kwargs["args"] == (str(eligible.id),)


@pytest.mark.django_db
def test_run_add_touches_only_the_run_it_was_posted_to(
    env_client,
    environment,
    finished_run,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
):
    """`completed_calls` and the dispatch are scoped to this run, not to every
    execution of the environment's run test."""
    template = _template("no_misselling", ["conversation"], tags=("Conversation",))
    mine = _call(finished_run, metadata=_graded())
    sibling_run = TestExecution.objects.create(
        run_test=environment.run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        scenario_ids=[str(environment.run_test.scenarios.first().id)],
    )
    theirs = _call(sibling_run, metadata=_graded())

    with django_capture_on_commit_callbacks(execute=True):
        response = _run_add(
            env_client, environment, finished_run, workspace, template.name
        )

    assert response.status_code == 202, response.content
    assert response.json() == {
        "queued": 1,
        "skipped_existing": 0,
        "skipped_in_flight": 0,
        "skipped_pending": 0,
        "completed_calls": 1,
    }, "the sibling run's completed call is not this run's"
    assert dispatch.call_count == 1
    assert dispatch.call_args.kwargs["args"] == (str(mine.id),)
    theirs.refresh_from_db()
    assert (
        "eval_queued" not in theirs.call_metadata
    ), "a sibling run's call is never stamped by another run's add"


@pytest.mark.django_db
def test_run_add_dispatches_per_call_with_skip_existing(
    env_client,
    environment,
    finished_run,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
):
    """One grading task per eligible call, in the exact argument shape
    `skip_existing=True` requires."""
    template = _template("no_misselling", ["conversation"], tags=("Conversation",))
    first_call = _call(finished_run, metadata=_graded())
    second_call = _call(finished_run, metadata=_graded())

    with django_capture_on_commit_callbacks(execute=True):
        response = _run_add(
            env_client, environment, finished_run, workspace, template.name
        )

    assert response.status_code == 202, response.content
    assert response.json()["queued"] == 2
    config = _bound_config(environment)
    dispatched = {
        call.kwargs["args"][0]: call.kwargs for call in dispatch.call_args_list
    }
    assert set(dispatched) == {str(first_call.id), str(second_call.id)}
    for call_execution_id, kwargs in dispatched.items():
        assert kwargs == {
            "args": (call_execution_id,),
            "kwargs": {
                "eval_config_ids": [str(config.id)],
                "skip_existing": True,
            },
        }
    assert all(call.args == () for call in dispatch.call_args_list)


@pytest.mark.django_db
def test_run_add_stamps_every_call_it_queues(
    env_client,
    environment,
    finished_run,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
):
    """Every queued call is stamped with an aware ISO-8601 timestamp inside
    the window, keyed by config id."""
    template = _template("no_misselling", ["conversation"], tags=("Conversation",))
    call_execution = _call(finished_run, metadata=_graded())

    before = timezone.now()
    with django_capture_on_commit_callbacks(execute=True):
        response = _run_add(
            env_client, environment, finished_run, workspace, template.name
        )
    after = timezone.now()

    assert response.status_code == 202, response.content
    config = _bound_config(environment)
    call_execution.refresh_from_db()
    stamps = call_execution.call_metadata["eval_queued"]
    assert set(stamps) == {str(config.id)}
    stamped = timezone.datetime.fromisoformat(stamps[str(config.id)])
    assert before <= stamped <= after
    assert after - stamped < EVAL_QUEUE_STAMP_WINDOW
    # The flags the eval pipeline owns are untouched.
    assert call_execution.call_metadata["eval_completed"] is True
    assert call_execution.call_metadata["eval_started"] is True


@pytest.mark.django_db
def test_nothing_is_dispatched_before_the_stamp_is_committed(
    env_client,
    environment,
    finished_run,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
):
    """The stamp commits inside the request's own transaction, before the
    deferred `on_commit` callback that would call `apply_async` has run at
    all."""
    template = _template("no_misselling", ["conversation"], tags=("Conversation",))
    call_execution = _call(finished_run, metadata=_graded())

    before = timezone.now()
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        response = _run_add(
            env_client, environment, finished_run, workspace, template.name
        )

    assert response.status_code == 202, response.content
    assert len(callbacks) == 1, "one on_commit callback for the whole batch"
    # The captured on_commit callback must be nameable by Django's robust
    # handler, which formats its log line with `func.__qualname__` -- a
    # `functools.partial` does not have one, a closure does.
    assert hasattr(callbacks[0], "__qualname__")
    dispatch.assert_not_called()
    config = _bound_config(environment)
    call_execution.refresh_from_db()
    assert set(call_execution.call_metadata["eval_queued"]) == {str(config.id)}, (
        "the stamp commits inside the request's own transaction, before the "
        "deferred callback that would dispatch anything has even run"
    )
    # The touch (`_touch_content`) already ran, inside the view, while
    # dispatch -- deferred to the on_commit callback above -- has not.
    environment.refresh_from_db()
    assert environment.content_updated_at >= before, (
        "the environment's clock is bumped during this request, while "
        "dispatch, which fires from a still-uncalled on_commit callback, "
        "has not run"
    )


@pytest.mark.django_db
def test_a_broker_failure_stops_further_dispatch_and_clears_the_remaining_stamps(
    env_client,
    environment,
    finished_run,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
    django_assert_num_queries,
):
    """The first hand-off failure in a batch stops the rest, and every
    not-yet-dispatched stamp, the failed call's own included, is cleared in
    one transaction rather than one per call."""
    template = _template("no_misselling", ["conversation"], tags=("Conversation",))
    calls = sorted(
        (_call(finished_run, metadata=_graded()) for _ in range(3)),
        key=lambda call_execution: call_execution.id,
    )
    first_call, second_call, third_call = calls

    def _side_effect(*args, **kwargs):
        if kwargs["args"] == (str(second_call.id),):
            raise RuntimeError("broker unreachable")

    dispatch.side_effect = _side_effect

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        response = _run_add(
            env_client, environment, finished_run, workspace, template.name
        )
    assert response.status_code == 202, response.content
    assert len(callbacks) == 1, "one on_commit callback for the whole batch"
    # Four statements for the whole batch, not one quartet per call:
    # `_clear_queued_stamps`'s own `transaction.atomic()` is a SAVEPOINT and
    # a RELEASE SAVEPOINT, plus one `SELECT ... FOR UPDATE` and one bulk
    # `UPDATE`.
    with django_assert_num_queries(4) as captured:
        callbacks[0]()
    # The unstamp path must not fetch the wide row for every locked call.
    select_sql = next(
        q["sql"] for q in captured.captured_queries if "FOR UPDATE" in q["sql"]
    )
    assert "analysis_data" not in select_sql, (
        "the unstamp path must not fetch the wide row for every call in a "
        "failed batch"
    )

    assert response.json()["queued"] == 3, (
        "the count is fixed when the stamp commits, not when dispatch "
        "reaches the broker -- a later failure cannot correct it"
    )
    dispatched_ids = {call.kwargs["args"][0] for call in dispatch.call_args_list}
    assert dispatched_ids == {str(first_call.id), str(second_call.id)}, (
        "the batch stops at the first failure -- the third call's job is "
        "never attempted"
    )
    config = _bound_config(environment)
    first_call.refresh_from_db()
    second_call.refresh_from_db()
    third_call.refresh_from_db()
    assert set(first_call.call_metadata.get("eval_queued", {})) == {
        str(config.id)
    }, "the one call whose job actually reached the broker keeps its stamp"
    for call_execution in (second_call, third_call):
        assert str(config.id) not in call_execution.call_metadata.get(
            "eval_queued", {}
        ), (
            "the failed call's own stamp, and every call the batch never "
            "attempted because it stopped, are cleared together in one "
            "transaction"
        )


# --- Run-level add, continued: skip reasons, refusals, the 404 -------------
# Same `django_capture_on_commit_callbacks(execute=True)` rule as above
# applies to every test below that posts and expects a dispatch outcome.

# The nine names the two cap tests bind. They are real catalog names, not
# invented ones: `add_selected_eval` refuses any name that is not a key of
# `evaluations/catalog/system_evals.yaml`, so a made-up placeholder name
# would come back 400 instead of filling the cap. All nine are offered for
# voice, and `_template`'s default `("Conversation",)` is a voice-relevant
# tag, so each one binds. `no_misselling` is deliberately not among them --
# the other tests in this section add that one.
CAP_FILLERS = [
    "advice_authority_boundary",
    "audio_quality",
    "claim_intake_accuracy",
    "conversation_coherence",
    "conversation_hallucination",
    "conversation_resolution",
    "customer_agent_clarification_seeking",
    "customer_agent_context_retention",
]
ONE_TOO_MANY = "customer_agent_human_escalation"


@pytest.mark.django_db
def test_run_add_skips_existing_pending_in_flight(
    env_client,
    environment,
    finished_run,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
):
    """The three reasons a finished call is passed over, each on its own,
    plus a fourth arm proving what does NOT pass it over: a
    `{"status": "pending"}` row is a stored row, not a verdict, so it must
    still be queued rather than counted `skipped_existing`."""
    template = _template("no_misselling", ["conversation"], tags=("Conversation",))
    graded = _call(finished_run, metadata=_graded())
    pending = _call(finished_run, metadata={"eval_started": True})
    in_flight = _call(finished_run, metadata=_graded())
    placeholder = _call(finished_run, metadata=_graded())

    # Bind the eval without queueing anything: every call is ineligible on the
    # first pass except `in_flight`, which is what stamps it.
    graded.call_metadata = {"eval_started": True}
    graded.save(update_fields=["call_metadata"])
    with django_capture_on_commit_callbacks(execute=True):
        first = _run_add(
            env_client, environment, finished_run, workspace, template.name
        )
    assert first.status_code == 202, first.content
    config = _bound_config(environment)
    graded.call_metadata = _graded()
    graded.eval_outputs = {str(config.id): _verdict()}
    graded.save(update_fields=["call_metadata", "eval_outputs"])
    # A `{"status": "pending"}` row is a stored row, not a verdict
    # (`utils/verdicts.py::has_stored_verdict`): this call must be queued,
    # not counted `skipped_existing`. A bare truthy `eval_outputs` read here
    # instead of the shared predicate would count it as already graded.
    placeholder.eval_outputs = {str(config.id): {"status": "pending"}}
    placeholder.call_metadata = _graded()
    placeholder.save(update_fields=["eval_outputs", "call_metadata"])
    dispatch.reset_mock()

    with django_capture_on_commit_callbacks(execute=True):
        response = _run_add(
            env_client, environment, finished_run, workspace, template.name
        )

    assert response.json() == {
        "queued": 1,
        "skipped_existing": 1,
        "skipped_in_flight": 1,
        "skipped_pending": 1,
        "completed_calls": 4,
    }
    assert dispatch.call_count == 1
    assert dispatch.call_args.kwargs["args"] == (str(placeholder.id),)
    pending.refresh_from_db()
    assert (
        "eval_queued" not in pending.call_metadata
    ), "F3: a call whose evaluations have not finished is never stamped either"
    graded.refresh_from_db()
    assert graded.eval_outputs == {
        str(config.id): _verdict()
    }, "P20/F1: the stored verdict is untouched"
    in_flight.refresh_from_db()
    assert set(in_flight.call_metadata["eval_queued"]) == {str(config.id)}


@pytest.mark.django_db
def test_run_add_never_dispatches_a_call_whose_evaluations_have_not_finished(
    env_client,
    environment,
    finished_run,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
):
    """No grading starts for a call whose evaluations have not finished;
    `eval_completed` absent and `eval_completed: False` are the same
    answer."""
    template = _template("no_misselling", ["conversation"], tags=("Conversation",))
    absent = _call(finished_run, metadata={"eval_started": True})
    explicit_false = _call(
        finished_run, metadata={"eval_started": True, "eval_completed": False}
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = _run_add(
            env_client, environment, finished_run, workspace, template.name
        )

    assert response.json() == {
        "queued": 0,
        "skipped_existing": 0,
        "skipped_in_flight": 0,
        "skipped_pending": 2,
        "completed_calls": 2,
    }
    dispatch.assert_not_called()
    for call_execution in (absent, explicit_false):
        call_execution.refresh_from_db()
        assert "eval_queued" not in call_execution.call_metadata


@pytest.mark.django_db
def test_run_add_repeated_within_ten_minutes_queues_nothing_new(
    env_client,
    environment,
    finished_run,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
):
    """Repeating the call within ten minutes queues nothing new; once the
    window lapses the same calls are eligible again."""
    template = _template("no_misselling", ["conversation"], tags=("Conversation",))
    call_execution = _call(finished_run, metadata=_graded())

    with django_capture_on_commit_callbacks(execute=True):
        first = _run_add(
            env_client, environment, finished_run, workspace, template.name
        )
    assert first.json()["queued"] == 1
    assert dispatch.call_count == 1

    with django_capture_on_commit_callbacks(execute=True):
        second = _run_add(
            env_client, environment, finished_run, workspace, template.name
        )
    assert second.status_code == 202, second.content
    assert second.json() == {
        "queued": 0,
        "skipped_existing": 0,
        "skipped_in_flight": 1,
        "skipped_pending": 0,
        "completed_calls": 1,
    }
    assert dispatch.call_count == 1, "the second add dispatched nothing"

    # Age the stamp past the window; the call becomes eligible again, which is
    # what makes a genuinely lost job recoverable.
    config = _bound_config(environment)
    call_execution.refresh_from_db()
    stale = timezone.now() - EVAL_QUEUE_STAMP_WINDOW - timedelta(seconds=1)
    call_execution.call_metadata["eval_queued"][str(config.id)] = stale.isoformat()
    call_execution.save(update_fields=["call_metadata"])

    with django_capture_on_commit_callbacks(execute=True):
        third = _run_add(
            env_client, environment, finished_run, workspace, template.name
        )
    assert third.json()["queued"] == 1
    assert dispatch.call_count == 2


@pytest.mark.django_db
def test_run_add_returns_the_environment_refusal_and_queues_nothing(
    env_client,
    environment,
    finished_run,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
):
    """Both bind refusals -- an ungradeable name (400) and a full
    environment (409) -- are returned unchanged, and neither stamps or
    dispatches anything."""
    from simulate.services.harness_evals import MOST_SELECTED_EVALS

    call_execution = _call(finished_run, metadata=_graded())

    with django_capture_on_commit_callbacks(execute=True):
        unknown = _run_add(
            env_client, environment, finished_run, workspace, "no_such_eval"
        )
    assert unknown.status_code == 400, unknown.content
    assert unknown.json()["detail"] == (
        "no_such_eval: not an eval this environment can be graded by"
    )

    assert (
        len(CAP_FILLERS) == MOST_SELECTED_EVALS
    ), "the cap must be filled exactly, so the next name is the one too many"
    for index, name in enumerate(CAP_FILLERS):
        _assert_catalog_key(name, listed=True)
        _template(name, ["conversation"], tags=("Conversation",), eval_id=index + 1)
        with django_capture_on_commit_callbacks(execute=True):
            filled = _run_add(env_client, environment, finished_run, workspace, name)
        assert filled.status_code == 202, (name, filled.content)
    dispatch.reset_mock()
    _assert_catalog_key(ONE_TOO_MANY, listed=True)
    _template(ONE_TOO_MANY, ["conversation"], tags=("Conversation",), eval_id=99)
    call_execution.refresh_from_db()
    before_stamps = dict(call_execution.call_metadata.get("eval_queued", {}))

    with django_capture_on_commit_callbacks(execute=True):
        full = _run_add(env_client, environment, finished_run, workspace, ONE_TOO_MANY)

    assert full.status_code == 409, full.content
    assert full.json()["detail"] == (
        f"{ONE_TOO_MANY}: an environment runs at most {MOST_SELECTED_EVALS} evals; "
        "remove one before adding another"
    )
    dispatch.assert_not_called()
    assert not SimulateEvalConfig.objects.filter(
        run_test_id=environment.run_test_id, name=ONE_TOO_MANY
    ).exists()
    call_execution.refresh_from_db()
    assert call_execution.call_metadata.get("eval_queued", {}) == before_stamps, (
        "a refused add stamps nothing: the stamps are keyed by config id, so "
        "comparing them before and after is the only way to see that"
    )


@pytest.mark.django_db
def test_run_add_refuses_a_bound_result_column_row(
    env_client,
    environment,
    finished_run,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
):
    """A name already bound as one of the harness's own result columns
    (empty ``mapping``) is refused with its own reason, and nothing is
    stamped or dispatched."""
    from simulate.services.alk_simulate_ingestion import (
        _get_or_create_harness_eval_config,
    )

    template = _template("no_misselling", ["conversation"], tags=("Conversation",))
    _call(finished_run, metadata=_graded())
    _get_or_create_harness_eval_config(finished_run.run_test, template, template.name)

    with django_capture_on_commit_callbacks(execute=True):
        response = _run_add(
            env_client, environment, finished_run, workspace, template.name
        )

    assert response.status_code == 400, response.content
    assert response.json()["detail"] == (
        f"{template.name} is bound as a result column and has nothing to grade"
    )
    dispatch.assert_not_called()


@pytest.mark.django_db
def test_run_add_at_the_cap_still_grades_an_eval_already_bound(
    env_client,
    environment,
    finished_run,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
):
    """Adding a name that is already bound is idempotent, and the
    idempotency scan runs before the cap check -- so a full environment can
    still have one of its own evals graded over a finished run."""
    from simulate.services.harness_evals import MOST_SELECTED_EVALS

    call_execution = _call(finished_run, metadata=_graded())
    assert len(CAP_FILLERS) == MOST_SELECTED_EVALS, "the cap must be filled exactly"
    for index, name in enumerate(CAP_FILLERS):
        _assert_catalog_key(name, listed=True)
        _template(name, ["conversation"], tags=("Conversation",), eval_id=index + 1)
        with django_capture_on_commit_callbacks(execute=True):
            filled = _run_add(env_client, environment, finished_run, workspace, name)
        assert filled.status_code == 202, (name, filled.content)
    call_execution.refresh_from_db()
    # Clear the stamps the eight adds left, so the re-add's own call is
    # eligible again rather than in flight.
    call_execution.call_metadata = _graded()
    call_execution.save(update_fields=["call_metadata"])
    dispatch.reset_mock()

    with django_capture_on_commit_callbacks(execute=True):
        again = _run_add(
            env_client, environment, finished_run, workspace, CAP_FILLERS[0]
        )

    assert again.status_code == 202, again.content
    assert again.json()["queued"] == 1
    assert dispatch.call_count == 1


@pytest.mark.django_db
def test_run_add_foreign_run_404(
    env_client,
    user,
    environment,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
):
    """404 when `execution_id` is not a run of this environment, and the eval
    is not bound, because the run is resolved before anything is bound."""
    template = _template("no_misselling", ["conversation"], tags=("Conversation",))

    other_job, _ = create_hosted_job(
        user.organization,
        _payload(),
        idempotency_key="env-evals-other-run",
        workspace=workspace,
    )
    capability = register_attempt(
        other_job.id, endpoint_base_url="https://platform.example"
    )
    provisioned = APIClient().post(
        f"{ATTEMPTS}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Other",
            "modality": "voice",
            "personas": [
                {
                    "scenario_key": "other-one",
                    "name": "Customer",
                    "situation": "Asks something else",
                    "outcome": "Agent answers",
                }
            ],
        },
        format="json",
        **_headers(capability),
    )
    assert provisioned.status_code == 200, provisioned.content
    other_job.refresh_from_db()
    foreign = TestExecution.objects.create(
        run_test=other_job.run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        scenario_ids=[str(other_job.run_test.scenarios.first().id)],
    )

    with django_capture_on_commit_callbacks(execute=True):
        stranger = env_client.post(
            f"{ENVIRONMENTS}/{environment.id}/runs/{foreign.id}/evaluations/",
            {"name": template.name},
            format="json",
            HTTP_X_WORKSPACE_ID=str(workspace.id),
        )
    with django_capture_on_commit_callbacks(execute=True):
        missing = env_client.post(
            f"{ENVIRONMENTS}/{environment.id}/runs/{uuid.uuid4()}/evaluations/",
            {"name": template.name},
            format="json",
            HTTP_X_WORKSPACE_ID=str(workspace.id),
        )

    assert stranger.status_code == 404, stranger.content
    assert stranger.json()["detail"] == "Run not found"
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Run not found"
    dispatch.assert_not_called()
    assert not SimulateEvalConfig.objects.filter(
        run_test_id=environment.run_test_id
    ).exists(), "a bad run id must leave nothing bound"


@pytest.mark.django_db
def test_run_add_refuses_an_environment_with_no_run_test(
    env_client, user, workspace, dispatch, django_capture_on_commit_callbacks
):
    """The "no run test yet" 409 reaches this route too, because it shares
    `_run_test_job`: an environment with no run test has no runs to
    grade."""
    job, _ = create_hosted_job(
        user.organization,
        _payload(),
        idempotency_key="env-evals-run-add-unbuilt",
        workspace=workspace,
    )
    with django_capture_on_commit_callbacks(execute=True):
        response = env_client.post(
            f"{ENVIRONMENTS}/{job.id}/runs/{uuid.uuid4()}/evaluations/",
            {"name": "no_misselling"},
            format="json",
            HTTP_X_WORKSPACE_ID=str(workspace.id),
        )
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Environment has no evaluations until it finishes building"
    )
    dispatch.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "run_status",
    [
        TestExecution.ExecutionStatus.CANCELLED,
        TestExecution.ExecutionStatus.CANCELLING,
    ],
)
def test_run_add_refuses_a_cancelled_run_before_binding_or_stamping(
    env_client,
    environment,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
    run_status,
):
    """A cancelled (or cancelling) run with finished calls is refused 409:
    the eval worker never grades a cancelled run, so a 202 here would count
    work that does not happen. Nothing is bound and no call is stamped, so
    the next click after the run is no longer cancelled is not hidden by the
    ten-minute stamp."""
    # A bindable eval, so that without the refusal this request would bind
    # and stamp -- which is what makes the "nothing left behind" assertions
    # below able to fail.
    template = _template("no_misselling", ["conversation"], tags=("Conversation",))
    run_test = environment.run_test
    scenario = run_test.scenarios.first()
    execution = TestExecution.objects.create(
        run_test=run_test,
        status=run_status,
        total_scenarios=1,
        scenario_ids=[str(scenario.id)],
    )
    call = _call(execution, metadata=_graded())
    bound_before = set(
        SimulateEvalConfig.objects.filter(run_test=run_test).values_list(
            "id", flat=True
        )
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = _run_add(
            env_client, environment, execution, workspace, template.name
        )

    assert response.status_code == 409, response.content
    assert response.json()["detail"] == "Run is cancelled; nothing will be graded"
    dispatch.assert_not_called()
    assert (
        set(
            SimulateEvalConfig.objects.filter(run_test=run_test).values_list(
                "id", flat=True
            )
        )
        == bound_before
    ), "the refusal must come before the bind"
    call.refresh_from_db()
    assert (
        "eval_queued" not in call.call_metadata
    ), "the refusal must come before the stamp"


@pytest.mark.django_db
def test_queue_eval_for_finished_calls_refuses_a_cancelled_run(environment):
    """The service backstop: no caller can stamp a cancelled run's calls."""
    from simulate.services.harness_evals import add_selected_eval
    from simulate.services.harness_run_evals import queue_eval_for_finished_calls

    run_test = environment.run_test
    scenario = run_test.scenarios.first()
    execution = TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.CANCELLED,
        total_scenarios=1,
        scenario_ids=[str(scenario.id)],
    )
    call = _call(execution, metadata=_graded())
    # `add_selected_eval` needs a real catalog template to bind against, the
    # same convention `test_run_add_counts` above uses.
    _template("no_misselling", ["conversation"], tags=("Conversation",))
    eval_config = add_selected_eval(run_test, "no_misselling", "voice")

    with pytest.raises(ValueError, match="does not grade a cancelled run"):
        queue_eval_for_finished_calls(execution, eval_config)

    call.refresh_from_db()
    assert "eval_queued" not in call.call_metadata


@pytest.mark.django_db
def test_run_add_moves_the_environment_clock_only_when_something_changed(
    env_client,
    environment,
    finished_run,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
):
    """Moves the environment's clock on a fresh bind (a), leaves it alone on
    a genuinely no-op repeat (b), and moves it again when a removed eval is
    revived, even though nothing new is queued (c) -- the regression guard:
    comparing `created_at` instead of `updated_at` misses this arm."""
    template = _template("no_misselling", ["conversation"], tags=("Conversation",))
    # Never eligible, so across every arm below only the bind itself -- not
    # a queued call -- can be what moves the clock.
    _call(finished_run, metadata={"eval_started": True})

    # The clock is never empty: `create_hosted_job` stamps it at creation and
    # registering scenarios bumps it (`services/hosted_harness.py:148,449`).
    # Take the fixture's value as the baseline every arm below is measured
    # against.
    environment.refresh_from_db()
    baseline = environment.content_updated_at
    assert baseline is not None

    # (a) fresh bind, `queued == 0` -- the clock must still move.
    before_a = timezone.now()
    assert baseline < before_a
    with django_capture_on_commit_callbacks(execute=True):
        first = _run_add(
            env_client, environment, finished_run, workspace, template.name
        )
    after_a = timezone.now()
    assert first.status_code == 202, first.content
    assert first.json() == {
        "queued": 0,
        "skipped_existing": 0,
        "skipped_in_flight": 0,
        "skipped_pending": 1,
        "completed_calls": 1,
    }
    config = _bound_config(environment, template.name)
    environment.refresh_from_db()
    assert environment.content_updated_at is not None
    assert before_a <= environment.content_updated_at <= after_a

    # (b) an immediate repeat of the same name, still `queued == 0` -- a
    # genuinely no-op click -- the clock must NOT move.
    after_first_add = environment.content_updated_at
    with django_capture_on_commit_callbacks(execute=True):
        second = _run_add(
            env_client, environment, finished_run, workspace, template.name
        )
    assert second.status_code == 202, second.content
    assert second.json() == first.json()
    environment.refresh_from_db()
    assert (
        environment.content_updated_at == after_first_add
    ), "a bind that saved nothing and queued nothing must not bump the clock"

    # (c) remove the eval, then re-add it from this run with its only
    # completed call already holding the verdict: `bind_eval_config` revives
    # the soft-deleted row under the SAME uuid5 id, so the old verdict still
    # matches and `queued` stays 0 -- but the revive DID save something, so
    # the clock must move.
    _call(
        finished_run,
        metadata=_graded(),
        eval_outputs={str(config.id): _verdict(template.name)},
    )
    remove = env_client.delete(
        f"{ENVIRONMENTS}/{environment.id}/evaluations/{config.id}/",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    assert remove.status_code == 204, remove.content
    environment.refresh_from_db()
    after_remove = environment.content_updated_at
    assert after_remove is not None and after_remove > after_first_add

    before_c = timezone.now()
    with django_capture_on_commit_callbacks(execute=True):
        third = _run_add(
            env_client, environment, finished_run, workspace, template.name
        )
    after_c = timezone.now()
    assert third.status_code == 202, third.content
    assert third.json() == {
        "queued": 0,
        "skipped_existing": 1,
        "skipped_in_flight": 0,
        "skipped_pending": 1,
        "completed_calls": 2,
    }
    environment.refresh_from_db()
    assert environment.content_updated_at is not None
    assert before_c <= environment.content_updated_at <= after_c
    assert environment.content_updated_at > after_remove, (
        "reviving a removed eval from inside a run is real content movement, "
        "even though it queues nothing new"
    )

    # (d) a repeat that binds nothing but queues a call that became eligible
    # since the last click -- the clock must move on the queueing alone.
    newly_eligible = _call(finished_run, metadata=_graded())
    environment.refresh_from_db()
    after_c_clock = environment.content_updated_at
    before_d = timezone.now()
    with django_capture_on_commit_callbacks(execute=True):
        fourth = _run_add(
            env_client, environment, finished_run, workspace, template.name
        )
    assert fourth.status_code == 202, fourth.content
    assert fourth.json()["queued"] == 1
    environment.refresh_from_db()
    assert (
        environment.content_updated_at > after_c_clock
    ), "an idempotent bind that queues a call is still content movement"
    assert environment.content_updated_at >= before_d
    newly_eligible.refresh_from_db()
    assert "eval_queued" in newly_eligible.call_metadata

    assert dispatch.call_count == 1


@pytest.mark.django_db
def test_available_not_visible_across_workspaces(env_client, user, workspace):
    """An environment that exists, but in a workspace the caller did not ask
    for, is a 404 — not a 403 and not a 200 leaking another tenant's
    data."""
    from accounts.models.workspace import Workspace

    other_workspace = Workspace.objects.create(
        name="Other workspace",
        organization=user.organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    foreign, _ = create_hosted_job(
        user.organization,
        _payload(),
        idempotency_key="env-evals-other-workspace",
        workspace=other_workspace,
    )
    response = _available(env_client, foreign, workspace)
    assert response.status_code == 404
    assert response.json()["detail"] == "Environment not found"


@pytest.mark.django_db
def test_run_add_not_visible_across_workspaces(
    env_client,
    user,
    environment,
    finished_run,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
):
    """`runs/{id}/evaluations/` has the same workspace isolation as
    `evaluations/available/`: a caller in another workspace gets a 404, not
    a leak of this workspace's run."""
    from accounts.models.workspace import Workspace

    other_workspace = Workspace.objects.create(
        name="Other workspace",
        organization=user.organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    template = _template("no_misselling", ["conversation"], tags=("Conversation",))
    with django_capture_on_commit_callbacks(execute=True):
        response = _run_add(
            env_client, environment, finished_run, other_workspace, template.name
        )
    assert response.status_code == 404
    assert response.json()["detail"] == "Environment not found"
    dispatch.assert_not_called()


@pytest.mark.django_db
def test_selected_required_keys_stays_aligned_with_the_stored_mapping(
    env_client, environment, workspace
):
    """`selected[]`'s `required_keys` must keep exactly one `inputs` row per
    name, even after the template is edited post-bind — not two
    `required_keys` and one `inputs` row for the same entry."""
    from simulate.services.harness_evals import add_selected_eval

    _assert_catalog_key("no_misselling", listed=True)
    template = _template("no_misselling", ["conversation"], tags=("Conversation",))
    add_selected_eval(environment.run_test, "no_misselling", "voice")

    template.config = {"required_keys": ["conversation", "context"]}
    template.save(update_fields=["config"])

    response = _detail(env_client, environment, workspace)
    assert response.status_code == 200, response.content
    (row,) = response.json()["evaluations"]["selected"]
    assert row["required_keys"] == ["conversation"]
    assert [entry["key"] for entry in row["inputs"]] == ["conversation"]
    assert len(row["inputs"]) == len(row["required_keys"])


@pytest.mark.django_db
def test_a_custom_eval_may_not_shadow_a_catalog_name(
    env_client, environment, workspace, user
):
    """`EvalTemplate.name` has no uniqueness constraint, so a tenant's custom
    eval can share a name with a catalog eval. The tenant's own row must win
    in every path — `available`, `add`, and provisioning.

    The custom row is planted before the system row: `EvalTemplate.clean()`
    blocks a custom row from being saved under a name an existing system row
    already has, so this is the only order that can exist.
    """
    _assert_catalog_key("conversation_coherence", listed=True)
    mine = _template(
        "conversation_coherence",
        ["conversation"],
        tags=("Agents",),
        owner="user",
        organization=user.organization,
        workspace=workspace,
    )
    system = _template(
        "conversation_coherence", ["conversation"], tags=("Conversation",)
    )

    available = _available(env_client, environment, workspace)
    assert available.status_code == 200, available.content
    entries = available.json()["evaluations"]
    matches = [entry for entry in entries if entry["name"] == "conversation_coherence"]
    assert len(matches) == 1, "one name, one addable thing"
    assert (
        matches[0]["source"] == "custom"
    ), "the tenant's own row must be the one listed"

    added = _add(env_client, environment, workspace, "conversation_coherence")
    assert added.status_code == 201, added.content
    (row,) = SimulateEvalConfig.objects.filter(
        run_test=environment.run_test, deleted=False
    )
    assert (
        row.eval_template_id == mine.id
    ), "add must bind the same row available listed"
    assert row.eval_template_id != system.id


@pytest.mark.django_db
def test_add_a_custom_eval_by_hand(env_client, environment, workspace, user):
    """A tenant's own custom eval — never a catalog key — can be added by
    hand."""
    mine = _template(
        "my_custom_conversation_check",
        ["conversation"],
        tags=("Agents",),
        owner="user",
        organization=user.organization,
        workspace=workspace,
    )
    response = _add(env_client, environment, workspace, mine.name)
    assert response.status_code == 201, response.content
    (row,) = SimulateEvalConfig.objects.filter(
        run_test=environment.run_test, deleted=False
    )
    assert row.eval_template_id == mine.id


@pytest.mark.django_db
def test_add_refuses_another_organizations_template(env_client, environment, workspace):
    """Another tenant's custom template must be refused at the add endpoint,
    not just silently absent from `available`."""
    from accounts.models.organization import Organization

    other_org = Organization.objects.create(name="A different organisation")
    theirs = _template(
        "their_custom_conversation_check",
        ["conversation"],
        tags=("Agents",),
        owner="user",
        organization=other_org,
    )
    response = _add(env_client, environment, workspace, theirs.name)
    assert response.status_code == 400, response.content
    assert response.json()["detail"] == (
        "their_custom_conversation_check: not an eval this environment can be graded by"
    )
    assert not SimulateEvalConfig.objects.filter(
        run_test=environment.run_test, deleted=False
    ).exists()


@pytest.mark.django_db
def test_selected_and_available_agree_on_required_keys_order(
    env_client, environment, workspace
):
    """`selected[]`'s `required_keys` must keep the template's stored order,
    the same order `available[]` reports — not `sorted(mapping)`. Two
    required keys, so the order is actually observable.
    """
    _assert_catalog_key("conversation_hallucination", listed=True)
    _template(
        "conversation_hallucination",
        ["conversation", "context"],
        tags=("Conversation",),
    )

    available = _available(env_client, environment, workspace)
    assert available.status_code == 200, available.content
    (offer,) = [
        entry
        for entry in available.json()["evaluations"]
        if entry["name"] == "conversation_hallucination"
    ]
    assert offer["required_keys"] == [
        "conversation",
        "context",
    ], "the offer's own stored order, or this test proves nothing"

    added = _add(env_client, environment, workspace, "conversation_hallucination")
    assert added.status_code == 201, added.content

    detail = _detail(env_client, environment, workspace)
    assert detail.status_code == 200, detail.content
    (row,) = detail.json()["evaluations"]["selected"]
    assert (
        row["required_keys"] == offer["required_keys"]
    ), "selected[] must report the same order available[] did"
    assert len(row["inputs"]) == len(row["required_keys"]), "L6's own invariant, kept"


@pytest.mark.django_db
def test_available_not_visible_across_organizations(env_client, workspace, user):
    """An environment belonging to an entirely different organisation must
    404 too, not merely be excluded by the workspace check."""
    from accounts.models.organization import Organization
    from accounts.models.workspace import Workspace

    other_org = Organization.objects.create(name="A wholly different organisation")
    other_workspace = Workspace.objects.create(
        name="Other org's workspace",
        organization=other_org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    foreign, _ = create_hosted_job(
        other_org,
        _payload(),
        idempotency_key="env-evals-other-org",
        workspace=other_workspace,
    )
    response = _available(env_client, foreign, workspace)
    assert response.status_code == 404
    assert response.json()["detail"] == "Environment not found"


@pytest.mark.django_db
def test_selected_inputs_never_outnumber_required_keys(
    env_client, environment, workspace
):
    """`selected[]`'s `inputs` must never outnumber `required_keys`, even
    when the template drops a required key after the bind."""
    from simulate.services.harness_evals import add_selected_eval

    _assert_catalog_key("conversation_hallucination", listed=True)
    template = _template(
        "conversation_hallucination",
        ["conversation", "context"],
        tags=("Conversation",),
    )
    add_selected_eval(environment.run_test, "conversation_hallucination", "voice")

    template.config = {"required_keys": ["conversation"]}
    template.save(update_fields=["config"])

    response = _detail(env_client, environment, workspace)
    assert response.status_code == 200, response.content
    (row,) = response.json()["evaluations"]["selected"]
    assert row["required_keys"] == ["conversation"]
    assert [entry["key"] for entry in row["inputs"]] == ["conversation"]
    assert len(row["inputs"]) == len(row["required_keys"])


@pytest.mark.django_db
def test_add_refuses_a_too_long_name_with_its_own_reason(environment, user, workspace):
    """The 255-character gate must report its own reason rather than reusing
    "not an eval this environment can be graded by", the same sentence
    `add_selected_eval` also raises for "another tenant's" and "wrong tag".
    """
    from simulate.services.harness_evals import (
        _MOST_NAME_CHARACTERS,
        EvalSelectionRefused,
        add_selected_eval,
    )

    over = "d" * (_MOST_NAME_CHARACTERS + 1)
    _template(
        over,
        ["conversation"],
        tags=("Agents",),
        owner="user",
        organization=user.organization,
        workspace=workspace,
    )
    with pytest.raises(EvalSelectionRefused) as excinfo:
        add_selected_eval(environment.run_test, over, "voice")
    assert excinfo.value.reason == "name is longer than 255 characters"


# Pure-function tests on the stamp helpers: no HTTP, no fixture, no DB.


def test_a_corrupt_stamp_reads_as_not_queued_instead_of_raising():
    """A well-formed-but-invalid stamp (``"2026-02-30T00:00:00"``, a real
    ISO-8601 shape naming a day that does not exist) reads as "not queued"
    rather than raising."""
    now = timezone.now()
    metadata = {EVAL_QUEUED_KEY: {"cfg": "2026-02-30T00:00:00"}}
    assert _queued_within_window(metadata, "cfg", now=now) is False


def test_the_window_guard_does_not_care_whether_the_config_id_is_a_uuid():
    """The stamp key is `str(config_id)` on read, matching write and clear,
    so a caller that passes a raw `UUID` still finds the stamp."""
    now, config_id = timezone.now(), uuid.uuid4()
    metadata = {EVAL_QUEUED_KEY: {str(config_id): now.isoformat()}}
    assert _queued_within_window(metadata, config_id, now=now) is True


def test_a_stamp_a_few_seconds_ahead_still_reads_as_queued():
    """A stamp a little *ahead* of `now`, well inside `EVAL_QUEUE_STAMP_SKEW`,
    still reads as queued -- clock skew between two web workers, not
    corruption."""
    now = timezone.now()
    ahead = (now + timedelta(seconds=2)).isoformat()
    metadata = {EVAL_QUEUED_KEY: {"cfg": ahead}}
    assert _queued_within_window(metadata, "cfg", now=now) is True


def test_a_stamp_far_in_the_future_does_not_lock_the_call_out_forever():
    """A garbage stamp like `"2999-01-01T00:00:00+00:00"` is far outside the
    skew band, so it reads as not queued rather than locking the call out
    for roughly 973 years."""
    now = timezone.now()
    metadata = {EVAL_QUEUED_KEY: {"cfg": "2999-01-01T00:00:00+00:00"}}
    assert _queued_within_window(metadata, "cfg", now=now) is False


def test_metadata_hands_back_a_writable_stamps_dict_even_when_the_column_is_corrupt():
    """`_metadata` coerces a non-dict `eval_queued` to `{}` rather than
    passing it through, so the recommended `setdefault` write pattern never
    raises."""
    from simulate.services.harness_run_evals import EVAL_QUEUED_KEY as _KEY
    from simulate.services.harness_run_evals import _metadata

    class _Call:
        call_metadata = {_KEY: "corrupt"}

    metadata = _metadata(_Call())
    # This line raised `TypeError` before the fix.
    metadata.setdefault(_KEY, {})["cfg"] = "2026-01-01T00:00:00+00:00"
    assert metadata[_KEY] == {"cfg": "2026-01-01T00:00:00+00:00"}


@pytest.mark.django_db
def test_clear_queued_stamps_finds_the_stamp_when_the_config_id_is_a_uuid(environment):
    """`_clear_queued_stamps` finds and clears a `UUID` config id's stamp,
    and removes only that config's key."""
    from simulate.models import CallExecution, TestExecution
    from simulate.services.harness_run_evals import _clear_queued_stamps

    run_test = environment.run_test
    scenario = run_test.scenarios.first()
    assert scenario is not None, "the provision fixture must leave a scenario"
    execution = TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        scenario_ids=[str(scenario.id)],
    )
    config_id = uuid.uuid4()
    call_execution = CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status=CallExecution.CallStatus.COMPLETED,
        call_metadata={
            EVAL_QUEUED_KEY: {
                str(config_id): "2026-01-01T00:00:00+00:00",
                "other": "2026-01-01T00:00:00+00:00",
            }
        },
    )

    _clear_queued_stamps([call_execution.id], config_id)  # raw UUID, not str()

    call_execution.refresh_from_db()
    assert call_execution.call_metadata[EVAL_QUEUED_KEY] == {
        "other": "2026-01-01T00:00:00+00:00"
    }
