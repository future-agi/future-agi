"""The environment's eval endpoints, over HTTP.

Contract: api_contracts/harness/eval-offer-backend-frontend.md v1.9 — §1 (entry
shape only), §2 P6, P6a, P7, §3 P8-P12 (`add_selected_eval`'s
three-way gate change is covered here too), §5 P16, P17, and §6's
ten-minute queued-stamp window (P22) via the `# --- Reviewer findings ---`
section below, plus §6's own selection transaction and its five counts
(P18-P22, F3), added by TH-8046 Tasks 5 and 6 further down this file. The
remove refusals (P13-P15) belong to TH-8045 and are not asserted here.
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

    These tests hard-code names against the pinned catalog lists (catalog
    contract P11): some must clear the first offer gate and some must not.
    ``offerable_eval_names()`` is the exact lookup the offer rule itself uses
    (``harness_evals.py::_is_listed_or_owned``), so a catalog edit that adds or
    drops one of these names breaks this guard loudly instead of leaving the
    test passing for a different reason than its name claims.
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


def _set_tool_call(client, job, workspace, enabled):
    return client.put(
        f"{ENVIRONMENTS}/{job.id}/evaluations/tool-call/",
        {"enable_tool_evaluation": enabled},
        format="json",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )


def _text_environment(user, workspace):
    """A built, TEXT-modality environment -- one the switch can be turned on
    for. `environment` above is voice-modality with no `AgentVersion`, which
    the switch's 409 check now refuses to turn on.
    """
    job, _ = create_hosted_job(
        user.organization,
        _payload(),
        idempotency_key="env-evals-tool-call-text",
        workspace=workspace,
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    response = APIClient().post(
        f"{ATTEMPTS}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Refunds (text)",
            "modality": "text",
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


def _file_receipt(job, *, scenario_key="refund-request"):
    """Post one accepted result receipt for a registered scenario.

    ``evaluations.results`` is built only from accepted receipts
    (services/harness_environment.py::_results); a test that wants to prove it
    is unchanged needs one filed first, or "unchanged" holds trivially of an
    empty list either way (frontend contract P17). Registers a fresh attempt
    (superseding the one the ``environment`` fixture used), begins the sealed
    scenario set, then files a minimal ``skipped`` receipt — the cheapest
    status the serializer accepts, needing no call or evaluation payload.
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
    """P2, P3, P6: one shape, one agent kind, sorted by name, every entry
    addable as it stands.

    P6's other half — that a bound eval is excluded — is proved by
    `test_available_subtracts_a_row_bound_under_the_harness_own_column_name`
    below, which subtracts under the harder of the two names a row can carry.
    """
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
    """P6a: the row ingestion makes for a harness result column is named after
    that column, not after the template, so both names must be subtracted —
    otherwise the eval is offered a second time under a second id.

    Two rows are planted, one exercising each half of the subtraction:
    ``no_misselling`` is bound under a column name that is neither offered
    template's own name, and ``audio_quality`` is bound under the column name
    ``conversation_coherence`` — a *different* template's own name (the
    template-name half is proved by the first row, the config-name half by
    this second one). Deleting either
    ``names.add(...)`` line from ``_bound_eval_names`` puts one name back in
    the offer and turns this red.
    """
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
    """P16: an empty-mapping row is bound but was never selected, so it is not
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


# --- Adding an eval by hand (frontend §3 P8-P12; M2) --------------------------
# `add_selected_eval` changed in three ways in this diff: the refusal gate
# gained `_is_listed_or_owned`/`_has_relevant_tag`, the idempotency check now
# matches the template's name as well as the config's, and the cap counts
# mapping-bearing rows only (owner decision Q1). None of the three had a test
# anywhere in the repo before this round; these four are the contract's own
# names for this block (§11 P8-P12).


@pytest.mark.django_db
def test_add_idempotent(env_client, environment, workspace):
    """P8: adding the same name twice returns 201 and leaves one config row.

    Also proves the idempotency check's second half (M2 change 2): a name
    already bound under a harness result-column name is not re-bound under
    the template's own name either — `add_selected_eval` matches a pick
    against both the config's stored name and its template's name, the same
    two names `available` already subtracts on (P6a).
    """
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
    """P9, P12: the listed/tag gate, the unmappable-input gate (M2 change 1),
    and an unknown body field.

    The 256-character `too_long` case below 400s, but through
    `HarnessEnvironmentSelectedEvalSerializer.name`'s `max_length=255`
    (`views/harness_environment.py`'s `@validated_request` decorator runs the
    serializer before the view body, let alone `add_selected_eval`, ever
    sees the request) — **not** through `add_selected_eval`'s own
    255-character gate (L1). Round 2, M2: that gate's own coverage is
    `test_add_refuses_a_name_too_long_for_the_bound_row`, below, which calls
    the service directly and cannot pass for this reason.
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
    """Round 2, M2: `add_selected_eval`'s own 255-character gate (L1),
    exercised with no HTTP serializer in the way to answer for it. Delete
    the `len(wanted) <= _MOST_NAME_CHARACTERS` guard in `add_selected_eval`
    and this test fails; `test_add_refusals`'s HTTP-level 256-character case
    would still pass either way, because the serializer refuses the request
    before the service is ever called — which is exactly the gap this test
    closes.

    A template is planted under the over-long name first: without one, the
    test would pass for the wrong reason (name simply not found).
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
    """M2 change 3 (owner decision Q1, frontend P10/P16): the cap of 8 counts
    mapping-bearing rows only. Five empty-mapping harness-column rows are
    bound first and must not eat into the cap; revert the cap line in
    `add_selected_eval` to count every bound row instead
    (`len(current) >= MOST_SELECTED_EVALS`) and the counter starts at the
    five already-bound rows, so the **fourth** `cap_fillers` add below 409s,
    not the eighth (L9: an earlier version of this docstring named the wrong
    one — the test itself was always failing at the right place).
    """
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
    """P11: adding an eval from here never grades a call that already
    completed. The add endpoint binds a config and touches nothing else —
    grading a finished call is the run-level add's job (§6, TH-8046), not
    this one's. Planted as a dispatch spy: if `add_evaluation` ever started
    dispatching the per-call grading task itself, this would catch it.
    """
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
    """P7: the check is 'no run test yet', not a build state."""
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
    """P7, the other half: an environment the caller cannot see is a 404, and a
    hand-edited id that is not a UUID must be a miss, not a 500.

    Neither id below belongs to a real environment, so this needs no
    `environment` fixture (Minor #22, round 2) — the fixture used to be
    requested and never used, costing a whole provision cycle per run for
    nothing.
    """
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
    """P17: `evaluations.results[]` is the receipt-based list and adding a
    platform eval leaves it byte-identical.

    Task 7 rewrites the function that builds `selected[]`, the sibling key in
    the same block, so this pins the one that must not move. Platform verdicts
    are read from call details (§7), never from here. A receipt is filed
    before the snapshot, so the comparison below is not `"[]" == "[]"`.
    """
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


# --- §6: add an eval from inside a run -------------------------------------
# Contract api_contracts/harness/eval-offer-backend-frontend.md v1.9 §6
# (P18, P18a, P19, P20, P21, P22) and F3; design §6; lld-4-add-from-run.puml.
#
# Every test that posts to this endpoint MUST take the `dispatch` fixture.
# Without it a real apply_async escapes into Temporal.
#
# MANDATORY (TH-8046): every test that
# posts here and then expects a dispatch outcome -- a stamp being queued,
# `dispatch` being called, or `dispatch.assert_not_called()` -- MUST run that
# POST inside `django_capture_on_commit_callbacks(execute=True)`. Every
# grading job this endpoint queues is scheduled with `transaction.on_commit`
# (`harness_run_evals.py::queue_eval_for_finished_calls`), which Django only
# runs once the OUTERMOST transaction actually commits; pytest-django's `db`
# fixture wraps each test in an atomic block that is rolled back, never
# committed, so a plain `@pytest.mark.django_db` HTTP test here would observe
# zero dispatch calls regardless of what the endpoint actually did --
# vacuously passing an "assert not called" and failing an "assert called" for
# the wrong reason. Precedent: `futureagi/tracer/tests/test_ch25_p3b_flip_gates.py:341`,
# `futureagi/tracer/tests/test_project.py:624`.
# `test_nothing_is_dispatched_before_the_stamp_is_committed` below uses
# `execute=False` instead -- its whole point is to prove the ordering, not
# just that dispatch eventually happens.


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

    Patching the attribute on the task object itself is what makes the
    service's function-local import see the patch -- the same idiom
    `test_add_never_grades_finished_calls` above uses for the environment-level
    add.
    """
    from unittest.mock import patch

    with patch(
        "simulate.services.test_executor._run_simulate_evaluations_task.apply_async"
    ) as spy:
        yield spy


@pytest.fixture
def finished_run(environment):
    """One finished run of this environment, with no calls yet.

    In production the attempt's `scenarios/` endpoint with `operation: begin`
    (`hosted_harness.py::begin_scenarios`) creates the execution and
    pre-allocates its calls. Here the row is built directly, because the
    point of these tests is to put each call in exactly one of the four
    states §6 distinguishes, which a real `begin` cannot do.
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
    """P19: the five counts partition the run's finished calls.

    One call of each kind, plus a failed call that is not a finished call at
    all and must not be counted.
    """
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
    """P19/P21: `completed_calls` and the dispatch are scoped to THIS execution,
    not to every execution of the environment's run test. A rerun keeps the same
    run test (`harness_provider.py`), so a sibling run's eligible calls must be
    neither counted nor stamped nor dispatched."""
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
    """P18a, verbatim: one task per eligible call, that exact argument shape.

    `args` is a one-element tuple, `eval_config_ids` a one-element list of
    strings, and `skip_existing` the literal True -- the flag TH-8045 taught
    the task to honour, and the only thing that makes a second grading of the
    same call harmless.
    """
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
    """P19: `queued` = the rest, each stamped now and dispatched.

    The stamp is keyed by config id so a second eval queued on the same call
    does not erase the first one's, and it is an aware ISO-8601 timestamp
    inside the window.
    """
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
    """The ordering itself, not just that dispatch eventually happens:
    `queue_eval_for_finished_calls` writes the stamp and schedules dispatch
    with `transaction.on_commit` inside the same transaction as the bind, so
    the stamp is durable before the callback that would call `apply_async`
    has run at all.

    `execute=False` captures the callback without running it: the stamp must
    already be visible in the database at that point, and `dispatch` must
    still be untouched.
    """
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
    """TH-8046: the first hand-off failure in a batch
    stops the rest -- a dead broker costs one timeout, not N -- and every
    not-yet-dispatched stamp, the failed call's own included, is cleared in
    one transaction rather than one per call.

    Uses `django_capture_on_commit_callbacks(execute=False)` and runs the one
    captured callback by hand, inside `django_assert_num_queries`, so the
    query count below covers only the callback (`_dispatch_batch_after_commit`
    is scheduled with `transaction.on_commit` and only runs once the
    request's transaction actually commits) and not the whole request.

    The batch dispatches in `id` order (`queue_eval_for_finished_calls`'s own
    `.order_by("id")`), so the three calls are sorted here first, and the
    second one by that order is made to fail -- the point being that the
    third is never attempted at all.
    """
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
    # The batch stops at the first failure and unstamps what it never sent in
    # ONE transaction, not one pair per call (TH-8046):
    # `_clear_queued_stamps` opens its own `transaction.atomic()`, nested
    # inside this test's already-open transaction, so entering and leaving it
    # is a real SAVEPOINT and a RELEASE SAVEPOINT, not free -- plus the one
    # `SELECT ... FOR UPDATE` and the one bulk `UPDATE` (`bulk_update`'s own
    # internal atomic block passes `savepoint=False`, so it adds no
    # statement of its own). Four statements, not one pair per call: three
    # eligible calls, two of them to unstamp -- a per-call clear would issue
    # its own SAVEPOINT/SELECT/UPDATE/RELEASE quartet for each of the two.
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


# --- §6, continued: skip reasons, refusals, the 404, and F3 -----------------
# Contract §11 rows `::test_run_add_skips_existing_pending_in_flight` and
# `::test_run_add_foreign_run_404`. Contract P18, P20, P21, P22; F3. Design
# §10's two run-level failure rows. Same mandatory
# `django_capture_on_commit_callbacks(execute=True)` rule as above applies to
# every test below that posts and expects a dispatch outcome.

# The nine names the two cap tests bind. They are real catalog names, not
# invented ones: `add_selected_eval` refuses any name that is not a key of
# `evaluations/catalog/system_evals.yaml`, so a made-up placeholder name
# would come back 400 instead of filling the cap. All nine are offered for
# voice (eval-catalog.md P11) and `_template`'s default `("Conversation",)` is
# a voice-relevant tag, so each one binds. `no_misselling` is deliberately
# not among them -- the other tests in this section add that one.
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
    """P19/P20/P22 and F3: the three reasons a finished call is passed over,
    each on its own, plus a fourth arm proving what does NOT pass it over --
    a `{"status": "pending"}` row is a stored row, not a verdict (contract
    F2), so it must still be queued rather than counted `skipped_existing`.
    """
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
    # A `{"status": "pending"}` row is a stored row, not a verdict (contract
    # F2, `utils/verdicts.py::has_stored_verdict`): this call must be queued,
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
    """F3, on its own: no endpoint here starts a grading for a call whose
    evaluations have not finished.

    `eval_completed` absent and `eval_completed: False` are the same answer:
    the eval task's `eval_started` latch would otherwise swallow the receipt's
    own dispatch (design §6).
    """
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
    """P22: repeating the call within ten minutes queues nothing new; once the
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
    """P18: a §3 refusal is returned unchanged and nothing is queued.

    Both refusals: a name the environment cannot be graded by (400, P9) and a
    full environment (409, P10). Neither may stamp or dispatch anything.
    """
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
    """P18b: a name already bound as one of
    the harness's own result columns (empty ``mapping``, ingestion-bound) is
    refused with its own reason -- not P9's "does not produce", which would
    be false here: the run demonstrably CAN fill this eval's inputs, the
    harness already reports it natively. Nothing is stamped or dispatched.
    """
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
    """P8 + P18: adding a name that is already bound is idempotent, and the
    idempotency scan runs before the cap check -- so a full environment can
    still have one of its own evals graded over a finished run.

    This is also the backend half of contract v1.9 P27's "Grade this run"
    action: in run mode TH-8047's picker lists the evals the environment
    ALREADY has and posts each one to this endpoint. `add_selected_eval`
    returns the existing config rather than raising (`harness_evals.py:616-625`,
    the scan sitting above the cap check at `:626`), so a bound name skips the
    bind and goes straight to queueing -- no special case is needed in the view,
    and this test is what proves it. P20 keeps the already-graded calls out and
    P22 bounds a repeat.
    """
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
    """P21: 404 when `execution_id` is not a run of this environment -- and the
    eval is not bound, because the run is resolved before anything is bound."""
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
    """P7/P10's 409 reaches this route too, because it shares `_run_test_job`:
    an environment with no run test has no runs to grade."""
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
def test_run_add_moves_the_environment_clock_only_when_something_changed(
    env_client,
    environment,
    finished_run,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
):
    """TH-8046: the touch-skip at
    `views/harness_environment.py:449` moves the environment's clock on a
    fresh bind (a), leaves it alone on a genuinely no-op repeat (b), and
    moves it again when a removed eval is revived by a run-level add, even
    though every completed call already holds its verdict and nothing new
    is queued (c). (c) is the regression guard: comparing `created_at`
    (write-once) instead of `updated_at` (written on both the fresh insert
    and the revive's save) misses this arm and fails today.
    """
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


# --- TH-8055: the tool-call evaluation switch --------------------------------


@pytest.mark.django_db
def test_tool_call_switch_is_read_from_the_detail(env_client, environment, workspace):
    """P31: `settings.enable_tool_evaluation` is a boolean on every detail, off
    by default, and it reports the run test's column rather than a constant.

    Failing scenario this catches: return a hard-coded `False` from `_settings`
    and every other test in this file still passes while the read surface lies
    about an environment whose switch is on.
    """
    first = _detail(env_client, environment, workspace)
    assert first.status_code == 200, first.content
    assert first.json()["settings"]["enable_tool_evaluation"] is False

    run_test = environment.run_test
    run_test.enable_tool_evaluation = True
    run_test.save(update_fields=["enable_tool_evaluation", "updated_at"])

    second = _detail(env_client, environment, workspace)
    assert second.status_code == 200, second.content
    assert second.json()["settings"]["enable_tool_evaluation"] is True


@pytest.mark.django_db
def test_tool_call_switch_reads_false_before_the_environment_is_built(
    env_client, user, workspace
):
    """P31: never null and never absent — an environment with no run test
    yet reads `false`, not `null`."""
    unbuilt, _ = create_hosted_job(
        user.organization,
        _payload(),
        idempotency_key="env-evals-tool-call-unbuilt-detail",
        workspace=workspace,
    )
    body = _detail(env_client, unbuilt, workspace)
    assert body.status_code == 200, body.content
    assert body.json()["settings"]["enable_tool_evaluation"] is False


@pytest.mark.django_db
def test_tool_call_switch_toggles(env_client, user, workspace):
    """P32: PUT sets the column and answers with the whole environment detail,
    already showing the new value, in both directions.

    Uses a TEXT-modality environment: the shared `environment` fixture is
    voice-modality with no `AgentVersion`, which the switch now refuses to
    turn on.
    """
    environment = _text_environment(user, workspace)
    on = _set_tool_call(env_client, environment, workspace, True)
    assert on.status_code == 200, on.content
    body = on.json()
    assert body["settings"]["enable_tool_evaluation"] is True
    # The 200 is the same body as GET {id}/, not a fragment: the client must
    # not have to refetch to learn anything else about the environment.
    assert set(body) == {
        "id",
        "overview",
        "contract",
        "world",
        "scenarios",
        "evaluations",
        "settings",
    }
    environment.run_test.refresh_from_db()
    assert environment.run_test.enable_tool_evaluation is True

    off = _set_tool_call(env_client, environment, workspace, False)
    assert off.status_code == 200, off.content
    assert off.json()["settings"]["enable_tool_evaluation"] is False
    environment.run_test.refresh_from_db()
    assert environment.run_test.enable_tool_evaluation is False

    # Turning it on and off again binds no eval and leaves the catalogue lists
    # alone: this is a switch, not an entry (contract v1.9 §13 opening).
    assert off.json()["evaluations"]["selected"] == []


@pytest.mark.django_db
def test_tool_call_switch_is_idempotent(env_client, user, workspace):
    """P34: setting the value it already has is a 200, not an error, and moves
    only the environment's content clock.

    There is no second row to create, so idempotency here means "the repeat is
    accepted and changes nothing else" -- including that it does not somehow
    flip the value back. Uses a TEXT-modality environment for the same reason
    `test_tool_call_switch_toggles` does.
    """
    environment = _text_environment(user, workspace)
    first = _set_tool_call(env_client, environment, workspace, True)
    assert first.status_code == 200, first.content
    environment.refresh_from_db()
    after_first = environment.content_updated_at

    second = _set_tool_call(env_client, environment, workspace, True)
    assert second.status_code == 200, second.content
    assert second.json()["settings"]["enable_tool_evaluation"] is True
    environment.run_test.refresh_from_db()
    assert environment.run_test.enable_tool_evaluation is True

    environment.refresh_from_db()
    assert environment.content_updated_at > after_first, (
        "_touch_content must fire on every accepted call, as the add and the "
        "remove do -- one rule for the list's clock"
    )


@pytest.mark.django_db
def test_tool_call_switch_refusals(env_client, environment, user, workspace):
    """P33: the same 404 and 409 as this endpoint's three siblings, and a 400
    for a body this switch cannot read."""
    import uuid

    missing = _set_tool_call(
        env_client, type("J", (), {"id": uuid.uuid4()})(), workspace, True
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Environment not found"

    nonsense = env_client.put(
        f"{ENVIRONMENTS}/not-a-uuid/evaluations/tool-call/",
        {"enable_tool_evaluation": True},
        format="json",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    assert nonsense.status_code == 404
    assert nonsense.json()["detail"] == "Environment not found"

    unbuilt, _ = create_hosted_job(
        user.organization,
        _payload(),
        idempotency_key="env-evals-tool-call-unbuilt",
        workspace=workspace,
    )
    no_run_test = _set_tool_call(env_client, unbuilt, workspace, True)
    assert no_run_test.status_code == 409
    assert no_run_test.json()["detail"] == (
        "Environment has no evaluations until it finishes building"
    )

    empty = env_client.put(
        f"{ENVIRONMENTS}/{environment.id}/evaluations/tool-call/",
        {},
        format="json",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    assert empty.status_code == 400, empty.content

    not_a_boolean = _set_tool_call(env_client, environment, workspace, "maybe")
    assert not_a_boolean.status_code == 400, not_a_boolean.content

    unknown_field = env_client.put(
        f"{ENVIRONMENTS}/{environment.id}/evaluations/tool-call/",
        {"enable_tool_evaluation": True, "unexpected": "field"},
        format="json",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    assert unknown_field.status_code == 400, unknown_field.content

    # Two more P33 clauses.
    null_value = env_client.put(
        f"{ENVIRONMENTS}/{environment.id}/evaluations/tool-call/",
        {"enable_tool_evaluation": None},
        format="json",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    assert null_value.status_code == 400, null_value.content

    # A malformed body 400s even against an environment the caller cannot
    # see -- request validation runs before the environment lookup.
    invisible_malformed = env_client.put(
        f"{ENVIRONMENTS}/{uuid.uuid4()}/evaluations/tool-call/",
        {"enable_tool_evaluation": "maybe"},
        format="json",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    assert invisible_malformed.status_code == 400, invisible_malformed.content

    # None of the refusals wrote anything.
    environment.run_test.refresh_from_db()
    assert environment.run_test.enable_tool_evaluation is False


@pytest.mark.django_db
def test_tool_call_switch_not_visible_across_workspaces(env_client, user, workspace):
    """P33's tenancy half: an environment that exists, but in a workspace the
    caller did not ask for, is a 404 -- and the write must not land.

    Failing scenario this catches: resolve the job with a bare
    `HostedHarnessJob.objects.get(id=...)` in the new action instead of going
    through `_run_test_job`/`_queryset`, and one workspace can switch on a
    judge that bills another workspace's calls.
    """
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
        idempotency_key="env-evals-tool-call-other-workspace",
        workspace=other_workspace,
    )
    response = _set_tool_call(env_client, foreign, workspace, True)
    assert response.status_code == 404
    assert response.json()["detail"] == "Environment not found"
    # The 404 is the whole signal, deliberately. `foreign` came straight from
    # `create_hosted_job` with no attempt and no scenario registration, so it
    # has no run test at all -- there is no column for a leaked write to land
    # in, and an assertion on `foreign.run_test` would pass no matter what the
    # endpoint did. What this test proves is the refusal itself: a bare
    # `HostedHarnessJob.objects.get(...)` would answer 409 here (the row
    # exists, it just has no run test), and 404 is the only answer that says
    # the caller may not see this environment at all.


def test_tool_call_switch_does_not_publish_its_internals_to_swagger():
    """TH-8055: `operation_description` on the PUT's `@validated_request` must
    exist, or drf-yasg falls back to the action's internal docstring -- which
    cites an internal file path and the contract's own "never committed"
    working-file name -- as the published `swagger.json` description.

    Failing scenario this catches: drop the `operation_description` keyword
    argument and `overrides["operation_description"]` raises `KeyError`
    instead of holding the one-sentence summary.
    """
    from simulate.views.harness_environment import HarnessEnvironmentViewSet

    overrides = HarnessEnvironmentViewSet.set_tool_call_evaluation._swagger_auto_schema
    # drf-yasg keys the overrides by HTTP method for a viewset action.
    overrides = overrides.get("put", overrides)
    description = overrides["operation_description"]
    assert "test_executor.py" not in description
    assert "eval-offer-backend-frontend" not in description


@pytest.mark.django_db
def test_tool_call_switch_refuses_on_for_a_versionless_voice_environment(
    env_client, environment, workspace
):
    """TH-8055: a hosted voice environment's agent definition has no
    `AgentVersion` (`_provision_agent_definition` never creates one), so the
    judge would silently never run. Turning the switch on for that shape is
    refused, 409; turning it off is always allowed.

    `environment` itself is exactly this shape -- voice, no version -- which
    is what makes it usable here without any extra setup.
    """
    on = _set_tool_call(env_client, environment, workspace, True)
    assert on.status_code == 409, on.content
    assert on.json()["detail"] == (
        "Tool-call evaluation is not available for a voice environment yet"
    )
    environment.run_test.refresh_from_db()
    assert environment.run_test.enable_tool_evaluation is False

    off = _set_tool_call(env_client, environment, workspace, False)
    assert off.status_code == 200, off.content
    assert off.json()["settings"]["enable_tool_evaluation"] is False


# --- Reviewer findings -------------------------------------------------------
# Every reviewer finding that survives verification becomes a test here, named
# after the finding (design §11). Do not delete this header; add below it.


@pytest.mark.django_db
def test_available_not_visible_across_workspaces(env_client, user, workspace):
    """P7: an environment that exists, but in a workspace the caller did not
    ask for, is a 404 — not a 403 and not a 200 leaking another tenant's data.

    Failing scenario this catches: drop the `scope_jobs(...)` wrapper around
    `_queryset` (views/harness_environment.py), and every other test in the
    repo still passes while one workspace could read another workspace's
    environment evals.
    """
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
    """TH-8046: `runs/{id}/evaluations/` has the same workspace isolation as
    `evaluations/available/` — a caller in another workspace gets the same
    404, not a 403 and not a leak of this workspace's run.
    """
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
    """L6: `selected[]`'s `required_keys` must keep frontend P1's invariant —
    exactly one `inputs` row per name in `required_keys` — even after the
    template is edited post-bind. Before the fix, `required_keys` came from
    the *live* template while `inputs` came from the *stored* mapping: adding
    a required key to the template after the eval was already selected would
    return two `required_keys` and one `inputs` row for the same entry.
    """
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


# --- Round 2 findings (M1, M2, M4, Lows, carried Minors) ---------------------


@pytest.mark.django_db
def test_a_custom_eval_may_not_shadow_a_catalog_name(
    env_client, environment, workspace, user
):
    """M1: `EvalTemplate.name` has no uniqueness constraint (`model_hub/models/evals_metric.py`
    declares no `Meta`/`UniqueConstraint` on it), and the product can end up
    with a tenant's custom eval sharing a name with a catalog eval — the
    catalog is periodically re-seeded (`seed_system_evals.py`, via
    `bulk_create`/`bulk_update`, which run no `clean()`/`full_clean()` at
    all), so a system eval can be added or renamed to a name a tenant already
    uses for their own custom eval without either side ever checking the
    other. (`EvalTemplate.clean()` *does* block a **custom** row from being
    saved under a name an **existing** system row already has — which is why
    this test plants the custom row first, then the system row: the
    model-level check only runs for `owner == "user"` saves and only looks
    backwards, so it cannot stop a system row arriving second under a name
    already in use.) Before the fix, `available` could list the name twice,
    `add` bound whichever row `.filter(name=wanted).first()`'s `-created_at`
    ordering returned, and provisioning's name-keyed `found` dict kept
    whichever row a `.filter(name__in=...)` queryset happened to return
    last — three different, uncoordinated answers. By owner decision, the
    tenant's own row wins; this test plants both, so ``system`` alone would
    fail every assertion below.
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
    """L10: the whole point of replacing the old `wanted not in
    offerable_eval_names()` gate with `_is_listed_or_owned` is that a
    tenant's own custom eval — never a catalog key — can now be added by
    hand. Nothing exercised the ownership half of that gate on its own
    before this test: `test_add_refusals` only proves the tag half
    (`toxicity` is refused by its tags whether or not `_is_listed_or_owned`
    runs at all).
    """
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
    """L10: another tenant's custom template must be refused at the add
    endpoint, not just silently absent from `available`."""
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
    """M4: `selected[]`'s `required_keys` must keep the template's *stored*
    order, the same order `available[]` reports — not `sorted(mapping)`.
    Frontend contract P1 promises `required_keys` in stored order and
    explicitly *not* aligned with `inputs` (which is sorted); a sorted
    `required_keys` on `selected[]` breaks both halves of that promise at
    once and makes the two lists byte-identical in order, which P1 forbids.
    Two required keys, so the order is actually observable — a one-key
    template (as `test_selected_required_keys_stays_aligned_with_the_stored_mapping`
    uses) cannot see this.
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
    """Carried Minor #20: only cross-workspace visibility had a test; an
    environment belonging to an entirely different organisation must 404
    too, not merely be excluded by the workspace check.
    """
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


# --- Round 3 findings (M1, L2) ------------------------------------------------


@pytest.mark.django_db
def test_selected_inputs_never_outnumber_required_keys(
    env_client, environment, workspace
):
    """M1: P1 in the direction round 2's M4 fix left open — the template
    drops a required key after the bind. Round 2's intersect
    (`entry["required_keys"] = [... if key in (config.mapping or {})]`) can
    only ever *shorten* `required_keys` relative to the template's live
    keys; `inputs` was still built from the whole stored mapping, so a
    template that drops a key it used to require left `inputs` with *more*
    rows than `required_keys` had names — the mirror image of L6's
    "template gains a key" case above. Narrowing the mapping `eval_entry`
    builds `inputs` from to the same live `required_keys` set (`services/
    harness_environment.py::_selected_evals`) closes it: revert that
    narrowing back to `dict(config.mapping or {})` and this fails with two
    `inputs` rows for one `required_keys` entry.
    """
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
    """L2: the 255-character gate must report its own reason rather than
    reusing "not an eval this environment can be graded by" — the same
    sentence `add_selected_eval` also raises for "another tenant's" and
    "wrong tag" (`test_add_refuses_another_organizations_template` above,
    `test_add_refusals`'s tag case). A direct service caller — the only
    caller this gate exists for (round 1's L1) — cannot otherwise tell a
    length problem from an eligibility one. A template is planted under the
    over-long name first, the same way
    `test_add_refuses_a_name_too_long_for_the_bound_row` does, so the test
    cannot pass merely because the name was never found.
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
    """TH-8046: a well-formed-but-invalid stamp must read as "not
    queued", not raise and 500 the whole run-level add.

    ``"2026-02-30T00:00:00"`` matches ISO-8601 shape but names a day that does
    not exist; Django's `parse_datetime` raises `ValueError` for exactly this
    case (it only returns `None` for input that is not well-formed at all).
    Before this fix nothing caught it here, so it escaped `_queued_within_window`
    and every call behind the corrupt one in Task 2's per-call loop.
    """
    now = timezone.now()
    metadata = {EVAL_QUEUED_KEY: {"cfg": "2026-02-30T00:00:00"}}
    assert _queued_within_window(metadata, "cfg", now=now) is False


def test_the_window_guard_does_not_care_whether_the_config_id_is_a_uuid():
    """TH-8046: the stamp key is `str(config_id)` on read, matching write
    and clear -- one normalisation, applied consistently.

    `SimulateEvalConfig.id` is a `UUIDField`; a caller that passes the raw
    `UUID` rather than `str(config.id)` must still find the stamp. Before
    this fix the raw `UUID` missed the string-keyed dict and P22's window
    silently stopped holding on every repeat click.
    """
    now, config_id = timezone.now(), uuid.uuid4()
    metadata = {EVAL_QUEUED_KEY: {str(config_id): now.isoformat()}}
    assert _queued_within_window(metadata, config_id, now=now) is True


def test_a_stamp_a_few_seconds_ahead_still_reads_as_queued():
    """TH-8046: a stamp a little *ahead* of `now` must
    still read as queued -- clock skew between two web workers, not
    corruption.

    Two seconds is well inside `EVAL_QUEUE_STAMP_SKEW` (one minute). Before
    this fix the window had no tolerance band at all
    (`return timedelta(0) <= elapsed < EVAL_QUEUE_STAMP_WINDOW`), so *any*
    stamp ahead of `now`, by any amount, read as not queued -- reopening the
    exact double-dispatch hole the window exists to close: a second worker,
    its clock a couple of seconds behind the first, would see this call as
    not-queued and queue (and dispatch) a second grading job for it.
    """
    now = timezone.now()
    ahead = (now + timedelta(seconds=2)).isoformat()
    metadata = {EVAL_QUEUED_KEY: {"cfg": ahead}}
    assert _queued_within_window(metadata, "cfg", now=now) is True


def test_a_stamp_far_in_the_future_does_not_lock_the_call_out_forever():
    """TH-8046: the window
    bounds a future stamp too -- `-EVAL_QUEUE_STAMP_SKEW <= (now - stamped) <
    window` -- not just "less than the window", and not an unbounded
    tolerance for clock skew either.

    A garbage stamp like `"2999-01-01T00:00:00+00:00"` is far outside even
    the one-minute skew band, so it must still read as not queued rather than
    blocking the call for roughly 973 years -- the "permanently
    unqueueable" outcome the module's own docstring says must not be
    possible -- instead of costing at most one ten-minute wait.
    """
    now = timezone.now()
    metadata = {EVAL_QUEUED_KEY: {"cfg": "2999-01-01T00:00:00+00:00"}}
    assert _queued_within_window(metadata, "cfg", now=now) is False


def test_metadata_hands_back_a_writable_stamps_dict_even_when_the_column_is_corrupt():
    """TH-8046: `_metadata` must coerce a non-dict
    `eval_queued` to `{}` rather than pass it through -- the write pattern
    its own docstring recommends (`result.setdefault(EVAL_QUEUED_KEY,
    {})[config_id] = ...`) must never raise.

    Before this fix a non-dict `eval_queued` (a stray string here) was
    copied through untouched, and `setdefault` on that string raised
    `TypeError: 'str' object does not support item assignment` -- which
    would have propagated out of Task 2's per-call loop and 500'd the whole
    run-level add on one corrupt row.
    """
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
    """TH-8046: `_clear_queued_stamps` -- the rollback for a
    stamp whose dispatch failed -- must find and clear a `UUID` config id's
    stamp, the same normalisation `_queued_within_window` already needed,
    and must remove only that config's key.

    Proved by removal: drop the `eval_config_id = str(eval_config_id)` line
    at the top of `_clear_queued_stamps` (`harness_run_evals.py:232`) and the
    raw `UUID` misses the string-keyed stamps dict, the function no-ops at
    its `eval_config_id not in stamps` guard, and the stamp survives --
    leaving the call reporting as already-queued for the rest of the
    ten-minute window while nothing is actually grading it, which is exactly
    the state the docstring says must not exist.
    """
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
