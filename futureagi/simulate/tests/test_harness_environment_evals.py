"""The environment's eval endpoints, over HTTP.

Contract: api_contracts/harness/eval-offer-backend-frontend.md v1.7 — §1 (entry
shape only), §2 P6, P6a, P7, §3 P8-P12 (fix round 1: `add_selected_eval`'s
three-way gate change is now covered here — M2), §5 P16, P17. The remove
refusals (P13-P15) and the run-level add (§6) belong to TH-8045 / TH-8046 and
are not asserted here.
"""

from __future__ import annotations

import json

import pytest
from rest_framework.test import APIClient

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import SimulateEvalConfig
from simulate.services.harness_evals import EVAL_RUN_CREDITS, offerable_eval_names
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
