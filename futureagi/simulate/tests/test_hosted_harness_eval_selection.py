"""The offer rule and the one list format."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient
from structlog.testing import capture_logs

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import SimulateEvalConfig
from simulate.services.harness_evals import (
    AGENT_KIND_CHAT,
    AGENT_KIND_VOICE,
    EVAL_RUN_CREDITS,
    MOST_SELECTED_EVALS,
    UnknownEvalSelection,
    briefing_evals,
    create_selected_eval_configs,
    offerable_eval_names,
    offered_evals,
    resolve_eval_mapping,
    runnable_eval_config_ids,
)
from simulate.services.hosted_harness import create_hosted_job, register_attempt

from .test_hosted_harness_channels import _headers, _payload

BASE = "/simulate/api/harness/attempts"

# Asserted against a freshly seeded database.
VOICE_OFFER = [
    "advice_authority_boundary",
    "audio_quality",
    "claim_intake_accuracy",
    "conversation_coherence",
    "conversation_hallucination",
    "conversation_resolution",
    "customer_agent_clarification_seeking",
    "customer_agent_context_retention",
    "customer_agent_conversation_quality",
    "customer_agent_human_escalation",
    "customer_agent_interruption_handling",
    "customer_agent_language_handling",
    "customer_agent_loop_detection",
    "customer_agent_objection_handling",
    "customer_agent_prompt_conformance",
    "customer_agent_query_handling",
    "customer_agent_task_completion",
    "customer_agent_termination_handling",
    "intake_field_accuracy",
    "lead_qualification_completeness",
    "no_misselling",
]
CHAT_OFFER = sorted(
    set(VOICE_OFFER) - {"audio_quality"} | {"bias_detection", "toxicity"}
)
# Legacy templates the catalog does not list.
UNLISTED = {
    "dead_air_detection",
    "voice_mail_detection",
    "voicemail_handling",
    "step_count",
}
# The order-of-drops tests bind real catalog names rather than invented ones:
# `create_selected_eval_configs` refuses any name the union briefing never
# carried, so a made-up placeholder would 400 before it could prove anything
# about the cap. Eight names from VOICE_OFFER, all `Conversation`-tagged in
# these tests so each fills on either kind of run, fill the cap exactly;
# a ninth is the one the cap drops.
CAP_FILLERS = [
    "advice_authority_boundary",
    "claim_intake_accuracy",
    "conversation_coherence",
    "conversation_hallucination",
    "conversation_resolution",
    "customer_agent_clarification_seeking",
    "customer_agent_context_retention",
    "customer_agent_conversation_quality",
]
ONE_TOO_MANY = "customer_agent_human_escalation"


def _template(
    name,
    required_keys,
    *,
    tags=("Conversation",),
    organization=None,
    workspace=None,
    eval_id=0,
    owner="system",
    eval_type="agent",
    visible_ui=True,
    deleted=False,
):
    return EvalTemplate.objects.create(
        name=name,
        description=f"{name} description",
        config={"required_keys": list(required_keys)},
        eval_tags=list(tags),
        eval_id=eval_id,
        organization=organization,
        workspace=workspace,
        owner=owner,
        eval_type=eval_type,
        visible_ui=visible_ui,
        deleted=deleted,
    )


def _offer(organization, workspace, modality):
    return {entry["name"] for entry in offered_evals(organization, workspace, modality)}


@pytest.fixture(autouse=True)
def _reset_offerable_eval_names_cache():
    """`harness_evals._offerable_eval_names_cache` is a module global, not
    rolled back by the test transaction the way a row is. Saving and
    restoring it here, the way `seeded_evals` below already does for the
    `system_evals_version` cache key, makes test isolation structural rather
    than resting on call order.
    """
    from simulate.services import harness_evals

    previous = harness_evals._offerable_eval_names_cache
    yield
    harness_evals._offerable_eval_names_cache = previous


@pytest.fixture
def seeded_evals(db):
    """A database seeded from the legacy definitions with the catalog's tags."""
    from model_hub.management.commands.seed_system_evals import seed_evals

    # The version cache is process-wide, not rolled back with the test
    # transaction: restore whatever was there so no later test sees this
    # test's value.
    previous = cache.get("system_evals_version")
    try:
        seed_evals(force=True)
        yield
    finally:
        if previous is None:
            cache.delete("system_evals_version")
        else:
            cache.set("system_evals_version", previous, timeout=None)


def _provision(client, capability, **extra):
    body = {
        "operation": "provision",
        "name": "Eval selection",
        "modality": "voice",
        "personas": [
            {
                "scenario_key": "refund-request",
                "name": "Customer",
                "situation": "Asks for a refund",
                "outcome": "Agent follows policy",
            }
        ],
    }
    body.update(extra)
    return client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        body,
        format="json",
        **_headers(capability),
    )


# --- The pinned lists ----------------------------------------------------------


@pytest.mark.django_db
def test_offer_matches_the_catalog(seeded_evals, organization, workspace):
    """The whole point of the change: the catalog decides, and it decides these.

    A tag edit that changes an offer must fail here. Both lists are sorted by
    name, which is also what `available` promises.
    """
    voice = [entry["name"] for entry in offered_evals(organization, workspace, "voice")]
    chat = [entry["name"] for entry in offered_evals(organization, workspace, "text")]
    assert voice == VOICE_OFFER, sorted(set(voice) ^ set(VOICE_OFFER))
    assert chat == CHAT_OFFER, sorted(set(chat) ^ set(CHAT_OFFER))
    assert len(voice) == 21
    assert len(chat) == 22


@pytest.mark.django_db
def test_unlisted_legacy_evals_are_never_offered(seeded_evals, organization, workspace):
    """Their legacy tags match; the catalog does not list them, so they are out."""
    for name in UNLISTED:
        assert EvalTemplate.no_workspace_objects.filter(
            name=name, owner="system", deleted=False
        ).exists(), f"{name} is not seeded; the test is not proving anything"
    for modality in ("voice", "text"):
        offered = _offer(organization, workspace, modality)
        assert not (offered & UNLISTED), sorted(offered & UNLISTED)


def test_cap_filler_names_are_still_catalog_keys():
    """`CAP_FILLERS`/`ONE_TOO_MANY` — the order-of-drops tests' own
    hard-coded names — are guarded here so a catalog edit that dropped one
    of them fails loudly, instead of silently failing an order-of-drops test
    for the wrong reason.
    """
    known = offerable_eval_names()
    for name in [*CAP_FILLERS, ONE_TOO_MANY]:
        assert (
            name in known
        ), f"{name} is no longer a catalog key; update CAP_FILLERS/ONE_TOO_MANY"


# --- A failed catalog read is never memoised ------------------------------------


def test_an_unreadable_catalog_is_retried_not_memoised(monkeypatch):
    """A cache that only ever holds a successful read: a transient read
    failure must not get memoised forever.

    Patches the `CATALOG_YAML` name in `seed_system_evals` rather than
    `pathlib.PosixPath.read_text` on the class, so the patch is narrowed to
    exactly this lookup rather than every `PosixPath` in the process.
    """
    from simulate.services import harness_evals
    import model_hub.management.commands.seed_system_evals as seed_system_evals

    class _UnreadableCatalog:
        def read_text(self, **_kwargs):
            raise OSError("gone")

    harness_evals._offerable_eval_names_cache = None
    monkeypatch.setattr(seed_system_evals, "CATALOG_YAML", _UnreadableCatalog())
    assert harness_evals.offerable_eval_names() == frozenset()
    monkeypatch.undo()
    assert "no_misselling" in harness_evals.offerable_eval_names()


def test_a_mis_encoded_catalog_is_retried_not_memoised(monkeypatch):
    """`.read_text(encoding="utf-8")` on a truncated or mis-encoded catalog
    raises `UnicodeDecodeError`, a `ValueError` subclass, not an `OSError` —
    it must surface as the documented empty offer, not an unhandled 500.
    """
    from simulate.services import harness_evals
    import model_hub.management.commands.seed_system_evals as seed_system_evals

    class _MisEncodedCatalog:
        def read_text(self, **_kwargs):
            raise UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "invalid start byte")

    harness_evals._offerable_eval_names_cache = None
    monkeypatch.setattr(seed_system_evals, "CATALOG_YAML", _MisEncodedCatalog())
    assert harness_evals.offerable_eval_names() == frozenset()
    monkeypatch.undo()
    assert "no_misselling" in harness_evals.offerable_eval_names()


def test_an_empty_catalog_is_not_memoised_either(monkeypatch):
    """A zero-byte or all-comments catalog parses to `{}` — a successful read
    of nothing — and that empty answer must not be cached either: the very
    next call, once the catalog is readable again, must see the real keys.
    """
    from simulate.services import harness_evals
    import model_hub.management.commands.seed_system_evals as seed_system_evals

    class _EmptyCatalog:
        def read_text(self, **_kwargs):
            return ""

    harness_evals._offerable_eval_names_cache = None
    monkeypatch.setattr(seed_system_evals, "CATALOG_YAML", _EmptyCatalog())
    assert harness_evals.offerable_eval_names() == frozenset()
    assert (
        harness_evals._offerable_eval_names_cache is None
    ), "an empty read must not be cached (L6)"
    monkeypatch.undo()
    assert "no_misselling" in harness_evals.offerable_eval_names()


def test_a_non_mapping_catalog_offers_nothing_rather_than_raising(monkeypatch):
    """A catalog that parses to a list or a scalar (a malformed edit, not an
    unreadable file) must not raise an unhandled `AttributeError` from
    `.items()` — that would surface as a 500 rather than the documented empty
    offer. Also not cached, for the same reason as the empty-dict case.
    """
    from simulate.services import harness_evals
    import model_hub.management.commands.seed_system_evals as seed_system_evals

    class _ListCatalog:
        def read_text(self, **_kwargs):
            return "- just\n- a\n- list\n"

    harness_evals._offerable_eval_names_cache = None
    monkeypatch.setattr(seed_system_evals, "CATALOG_YAML", _ListCatalog())
    assert harness_evals.offerable_eval_names() == frozenset()
    assert harness_evals._offerable_eval_names_cache is None
    monkeypatch.undo()
    assert "no_misselling" in harness_evals.offerable_eval_names()


def test_offered_templates_sorts_even_when_the_queryset_does_not(monkeypatch):
    """`offered_templates`'s own Python sort (`pairs.sort(...)`) must bite
    independent of the database's order. Stubbing `_visible_templates` to
    hand back plain Python objects in reverse order is the only way to prove
    the function's own sort is doing the work, since there is no queryset
    here to unsort.
    """
    from types import SimpleNamespace

    from simulate.services import harness_evals

    reversed_order = [
        SimpleNamespace(
            name=name,
            owner="system",
            organization_id=None,
            eval_tags=["Conversation"],
            config={"required_keys": ["conversation"]},
        )
        for name in ("zebra_eval", "no_misselling", "audio_quality")
    ]
    monkeypatch.setattr(
        harness_evals, "_visible_templates", lambda *_a, **_kw: reversed_order
    )
    monkeypatch.setattr(
        harness_evals,
        "offerable_eval_names",
        lambda: frozenset(t.name for t in reversed_order),
    )
    pairs = harness_evals.offered_templates(None, None, "voice")
    assert [template.name for template, _mapping in pairs] == [
        "audio_quality",
        "no_misselling",
        "zebra_eval",
    ], "offered_templates must sort its own output, not trust the input order"


# --- _pick_winner's tiebreak -----------------------------------------------------


def test_pick_winner_breaks_a_tie_between_two_of_a_tenants_own_templates_by_created_at():
    """`_pick_winner`'s own loop only ever replaces a `system` winner with a
    non-system one; between two equally specific candidates — here, both a
    tenant's own custom templates sharing one name — the newest `created_at`
    must win rather than whichever element the candidate list puts first.
    `SimpleNamespace` isolates `_pick_winner`'s own logic from the
    database's default ordering, so this cannot pass by accident of a
    queryset's own order.
    """
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from simulate.services import harness_evals

    older = SimpleNamespace(
        id="older", owner="user", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    newer = SimpleNamespace(
        id="newer", owner="user", created_at=datetime(2026, 1, 2, tzinfo=timezone.utc)
    )

    assert harness_evals._pick_winner([older, newer]).id == "newer"
    assert harness_evals._pick_winner([newer, older]).id == "newer", (
        "the newest must win regardless of which element the candidate "
        "list puts first"
    )


# --- The input table -------------------------------------------------------------


@pytest.mark.django_db
def test_a_voice_conversation_binds_to_the_combined_recording():
    """The whole-conversation source for a spoken call is the combined recording,
    which is what the recording-slot guard names as correct; a chat run has no
    recording and uses the transcript."""
    template = _template("customer_agent_conversation_quality", ["conversation"])
    assert resolve_eval_mapping(template, "voice") == {"conversation": "voice_recording"}
    assert resolve_eval_mapping(template, "text") == {"conversation": "transcript"}


@pytest.mark.django_db
def test_both_prompt_key_names_resolve_to_the_agent_prompt():
    conformance = _template(
        "customer_agent_prompt_conformance", ["system_prompt", "conversation"]
    )
    completion = _template(
        "customer_agent_task_completion", ["agent_prompt", "conversation"]
    )
    assert resolve_eval_mapping(conformance, "voice")["system_prompt"] == "agent_prompt"
    assert resolve_eval_mapping(completion, "voice")["agent_prompt"] == "agent_prompt"


@pytest.mark.django_db
def test_context_is_filled_with_the_agent_instructions():
    """A simulated call has no retrieval context, so the eval judges the
    agent against its own prompt. This is what brings
    `conversation_hallucination` into the offer."""
    template = _template("conversation_hallucination", ["conversation", "context"])
    for modality in ("voice", "text"):
        assert resolve_eval_mapping(template, modality)["context"] == "agent_prompt"


@pytest.mark.django_db
def test_the_input_key_reads_the_scenario_situation():
    template = _template("custom_input_eval", ["input"])
    for modality in ("voice", "text"):
        assert resolve_eval_mapping(template, modality) == {
            "input": "scenario_columns.situation.value"
        }


@pytest.mark.django_db
def test_voice_output_reads_the_transcript_not_the_recording():
    """Changed from today. The judge reads text, and no offered eval asks for
    `output` on voice, so no stored mapping migrates."""
    template = _template("some_output_eval", ["output"])
    assert resolve_eval_mapping(template, "voice") == {"output": "transcript"}
    assert resolve_eval_mapping(template, "text") == {"output": "transcript"}


@pytest.mark.django_db
def test_audio_keys_are_voice_only():
    for key in ("input_audio", "audio"):
        template = _template(f"eval_wanting_{key}", [key], tags=("Audio",))
        assert resolve_eval_mapping(template, "voice") == {key: "voice_recording"}
        assert resolve_eval_mapping(template, "text") is None


@pytest.mark.django_db
def test_a_template_with_an_unmappable_key_is_refused_rather_than_half_bound():
    """A partial mapping would hand the evaluator an empty variable, and an eval
    scoring nothing still returns a confident verdict."""
    template = _template("customer_agent_odd", ["conversation", "retrieved_context"])
    assert resolve_eval_mapping(template, "voice") is None


@pytest.mark.django_db
def test_an_eval_asking_for_nothing_has_no_mapping():
    """A custom code eval has no required keys; there is nothing to fill."""
    template = _template("custom_code_eval", [], eval_type="code")
    assert resolve_eval_mapping(template, "voice") is None


def test_every_table_source_has_a_label():
    """The frontend never computes a label, so a source without one is a
    blank row in the picker."""
    from simulate.services.harness_evals import (
        _LABEL_BY_SOURCE,
        _SOURCE_BY_KEY_TEXT,
        _SOURCE_BY_KEY_VOICE,
    )

    for table in (_SOURCE_BY_KEY_VOICE, _SOURCE_BY_KEY_TEXT):
        missing = sorted(set(table.values()) - set(_LABEL_BY_SOURCE))
        assert missing == [], missing
    assert set(_LABEL_BY_SOURCE.values()) == {
        "Call recording",
        "Transcript",
        "Agent instructions",
        "Scenario situation",
    }


def test_the_agent_kinds_match_the_read_model():
    from simulate.services.harness_environment import AGENT_TYPE_CHAT, AGENT_TYPE_VOICE

    assert (AGENT_KIND_VOICE, AGENT_KIND_CHAT) == (AGENT_TYPE_VOICE, AGENT_TYPE_CHAT)


# --- The gates ---------------------------------------------------------------


@pytest.mark.django_db
def test_an_unlisted_built_in_is_never_offered(organization, workspace):
    """A system-owned name the catalog does not list is out, whatever its tags."""
    _template("customer_agent_single_choice", ["conversation"], tags=("Agents", "Voice"))
    assert "customer_agent_single_choice" not in _offer(organization, workspace, "voice")


@pytest.mark.django_db
def test_a_listed_built_in_without_a_relevant_tag_is_not_offered(
    organization, workspace
):
    """A listed eval not meant for simulated conversations carries none of
    the relevant tags, and no other mechanism withholds it."""
    _template("no_misselling", ["conversation"], tags=("Insurance", "Accuracy"))
    assert "no_misselling" not in _offer(organization, workspace, "voice")
    assert "no_misselling" not in _offer(organization, workspace, "text")


@pytest.mark.django_db
def test_the_tag_sets_differ_by_kind(organization, workspace):
    _template("audio_quality", ["conversation"], tags=("Audio",))
    _template("toxicity", ["output"], tags=("Chatbot behaviors",))
    _template("conversation_coherence", ["conversation"], tags=("Agents",))
    assert _offer(organization, workspace, "voice") == {
        "audio_quality",
        "conversation_coherence",
    }
    assert _offer(organization, workspace, "text") == {
        "toxicity",
        "conversation_coherence",
    }


@pytest.mark.django_db
def test_tags_are_matched_case_insensitively_and_trimmed(organization, workspace):
    """Tags on the create/edit form are free text."""
    _template("no_misselling", ["conversation"], tags=("  chatbot BEHAVIORS ",))
    assert "no_misselling" in _offer(organization, workspace, "text")


@pytest.mark.django_db
def test_a_draft_is_never_offered(organization, workspace):
    _template(
        "no_misselling", ["conversation"], tags=("Conversation",), visible_ui=False
    )
    assert _offer(organization, workspace, "voice") == set()


@pytest.mark.django_db
def test_a_deleted_template_is_never_offered(organization, workspace):
    """The deleted half of the not-a-draft-and-not-deleted gate, mirroring
    the draft test above."""
    _template("no_misselling", ["conversation"], tags=("Conversation",), deleted=True)
    assert _offer(organization, workspace, "voice") == set()


@pytest.mark.django_db
def test_a_name_too_long_for_a_bound_row_is_never_offered(organization, workspace):
    """`EvalTemplate.name` allows 2000 characters but the bound row stores it
    in `SimulateEvalConfig.name`, a 255-character column. Offering a longer
    name would hand the picker a row that fails at insert. Custom evals on
    purpose: they clear every other gate, so the length is the only thing
    left that can withhold them.
    """
    from simulate.services.harness_evals import _MOST_NAME_CHARACTERS

    at_limit = "a" * _MOST_NAME_CHARACTERS
    over_limit = "b" * (_MOST_NAME_CHARACTERS + 1)
    for name in (at_limit, over_limit):
        _template(
            name,
            ["conversation"],
            tags=("Agents",),
            owner="user",
            organization=organization,
            workspace=workspace,
        )
    offered = _offer(organization, workspace, "voice")
    assert at_limit in offered, "255 characters fit the bound row and must be offered"
    assert over_limit not in offered
    assert (
        _MOST_NAME_CHARACTERS == SimulateEvalConfig._meta.get_field("name").max_length
    ), "the guard must match the column it protects, not just itself"


@pytest.mark.django_db
def test_a_custom_eval_of_this_organisation_is_offered_under_the_same_rule(
    organization, workspace
):
    """Custom evals are not in the catalog; ownership is their gate."""
    _template(
        "my_own_eval",
        ["conversation"],
        tags=("Agents",),
        owner="user",
        organization=organization,
        workspace=workspace,
    )
    assert "my_own_eval" in _offer(organization, workspace, "voice")


@pytest.mark.django_db
def test_a_custom_eval_using_a_name_no_source_fills_is_not_offered(
    organization, workspace
):
    """Its required keys are whatever variable names the author typed; only
    the table's left column can be filled."""
    _template(
        "my_retrieval_eval",
        ["retrieved_documents"],
        tags=("Agents",),
        owner="user",
        organization=organization,
        workspace=workspace,
    )
    assert "my_retrieval_eval" not in _offer(organization, workspace, "voice")


@pytest.mark.django_db
def test_another_tenants_template_is_never_offered(organization, workspace):
    from accounts.models import Organization

    other = Organization.objects.create(name="other-tenant")
    _template(
        "customer_agent_private",
        ["conversation"],
        tags=("Agents",),
        owner="user",
        organization=other,
    )
    _template("conversation_coherence", ["conversation"], tags=("Agents",))
    assert _offer(organization, workspace, "voice") == {"conversation_coherence"}


@pytest.mark.django_db
def test_a_custom_eval_in_another_workspace_is_not_offered(
    organization, workspace, user
):
    """The offer rule's workspace half: an `EvalTemplate` in a different
    workspace of the *same* organisation must be excluded by
    `_visible_templates`' `workspace_scope`.
    """
    from accounts.models.workspace import Workspace

    other_workspace = Workspace.objects.create(
        name="A different workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    _template(
        "my_other_workspace_eval",
        ["conversation"],
        tags=("Agents",),
        owner="user",
        organization=organization,
        workspace=other_workspace,
    )
    assert "my_other_workspace_eval" not in _offer(organization, workspace, "voice")


@pytest.mark.django_db
def test_a_raw_organization_id_raises_instead_of_silently_hiding_custom_evals(
    organization, workspace
):
    """`_is_listed_or_owned` must fail loudly, not silently empty the custom
    half of the offer, when `organization` is not an object with an `.id` —
    for example a bare UUID passed by mistake instead of the `Organization`
    instance every caller passes today. The type check runs before the
    system-owner branch, so a bare id raises for a system-owned template
    too, not just for the custom-template call; both branches are asserted
    below.
    """
    from simulate.services.harness_evals import _is_listed_or_owned

    template = _template(
        "my_own_eval",
        ["conversation"],
        tags=("Agents",),
        owner="user",
        organization=organization,
        workspace=workspace,
    )
    with pytest.raises(TypeError):
        _is_listed_or_owned(template, organization.id, offerable_eval_names())
    # The mistake is loud for a system-owned template too: the type check
    # runs unconditionally, before the branch that would otherwise never
    # read `organization` at all.
    builtin = _template("conversation_coherence", ["conversation"], tags=("Agents",))
    with pytest.raises(TypeError):
        _is_listed_or_owned(builtin, organization.id, offerable_eval_names())
    # `None` is still explicitly allowed on either branch — only a value that
    # looks like neither an `Organization` nor the absence of one is refused.
    assert _is_listed_or_owned(builtin, None, offerable_eval_names()) is (
        "conversation_coherence" in offerable_eval_names()
    )


# --- The entry format -----------------------------------------------------------


@pytest.mark.django_db
def test_entry_shape(organization, workspace):
    """The whole-dict equality pins the key set as well as the values, so no
    separate key-set assertion is needed: a field added or renamed fails
    here.
    """
    _template(
        "no_misselling",
        ["agent_prompt", "conversation"],
        tags=("Agents", "Conversation", "Voice", "Chatbot behaviors", "Insurance"),
    )
    (entry,) = offered_evals(organization, workspace, "voice")
    assert entry == {
        "name": "no_misselling",
        "description": "no_misselling description",
        "source": "system",
        "tags": ["Agents", "Conversation", "Voice", "Chatbot behaviors", "Insurance"],
        "required_keys": ["agent_prompt", "conversation"],
        "agent_type": "voice",
        "modality": "voice",
        "credits_per_run": EVAL_RUN_CREDITS,
        "charges_judge_tokens": True,
        "inputs": [
            {"key": "agent_prompt", "source": "agent_prompt", "label": "Agent instructions"},
            {"key": "conversation", "source": "voice_recording", "label": "Call recording"},
        ],
    }


@pytest.mark.django_db
def test_required_keys_keeps_stored_order_and_inputs_is_sorted(
    organization, workspace
):
    """The two lists are not aligned; pair them by key, never by position."""
    _template(
        "conversation_hallucination",
        ["conversation", "context"],
        tags=("Conversation",),
    )
    (entry,) = offered_evals(organization, workspace, "voice")
    assert entry["required_keys"] == ["conversation", "context"]
    assert [row["key"] for row in entry["inputs"]] == ["context", "conversation"]


@pytest.mark.django_db
def test_a_custom_entry_reports_its_source_and_kind(organization, workspace):
    _template(
        "my_own_eval",
        ["conversation"],
        tags=("Agents",),
        owner="user",
        organization=organization,
        workspace=workspace,
    )
    (entry,) = offered_evals(organization, workspace, "text")
    assert entry["source"] == "custom"
    assert entry["agent_type"] == "chat"
    assert entry["modality"] == "text"


@pytest.mark.django_db
def test_cost_fields_follow_the_eval_type(organization, workspace):
    """Nothing is free. Every eval costs `EVAL_RUN_CREDITS`; only a judged
    eval — `eval_type` `llm` or `agent` — additionally charges judge tokens,
    derived from `eval_type`, no new field for it."""
    _template(
        "my_code_eval",
        ["conversation"],
        tags=("Agents",),
        owner="user",
        organization=organization,
        workspace=workspace,
        eval_type="code",
    )
    _template("conversation_coherence", ["conversation"], tags=("Agents",))
    cost = {
        entry["name"]: (entry["credits_per_run"], entry["charges_judge_tokens"])
        for entry in offered_evals(organization, workspace, "voice")
    }
    assert cost == {
        "my_code_eval": (EVAL_RUN_CREDITS, False),
        "conversation_coherence": (EVAL_RUN_CREDITS, True),
    }
    for entry in offered_evals(organization, workspace, "voice"):
        assert "metered" not in entry


@pytest.mark.django_db
def test_available_is_sorted_by_name(organization, workspace):
    for name in ("no_misselling", "audio_quality", "conversation_coherence"):
        _template(name, ["conversation"], tags=("Conversation",))
    names = [entry["name"] for entry in offered_evals(organization, workspace, "voice")]
    assert names == sorted(names)
    assert names == ["audio_quality", "conversation_coherence", "no_misselling"]


# --- The launch briefing ------------------------------------------------------


@pytest.mark.django_db
def test_briefing_entries_keep_the_sandbox_fields(organization, workspace):
    """The four unchanged fields, the four new ones, no agent kind, no
    resolved inputs, and `any` exactly for names both lists carry."""
    _template("conversation_coherence", ["conversation"], tags=("Conversation",))
    _template("audio_quality", ["input_audio"], tags=("Audio",))
    _template("toxicity", ["output"], tags=("Chatbot behaviors",))

    entries = briefing_evals(organization, workspace)
    by_name = {entry["name"]: entry for entry in entries}
    assert set(by_name) == {"conversation_coherence", "audio_quality", "toxicity"}
    for entry in entries:
        # The whole-set equality already implies `agent_type` and `inputs`
        # are absent; a separate assert for each would be redundant.
        assert set(entry) == {
            "name",
            "description",
            "required_keys",
            "modality",
            "source",
            "tags",
            "credits_per_run",
            "charges_judge_tokens",
        }
    assert by_name["conversation_coherence"]["modality"] == "any"
    assert by_name["audio_quality"]["modality"] == "voice"
    assert by_name["toxicity"]["modality"] == "text"


@pytest.mark.django_db
def test_briefing_offers_each_name_once(organization, workspace):
    """Two entries for one name made the guest refuse the model's correct
    choices."""
    from collections import Counter

    _template("conversation_coherence", ["conversation"], tags=("Conversation",))
    names = Counter(entry["name"] for entry in briefing_evals(organization, workspace))
    assert [name for name, count in names.items() if count > 1] == []
    assert "conversation_coherence" in names


@pytest.mark.django_db
def test_briefing_for_an_authored_modality_is_the_same_shape(organization, workspace):
    _template("audio_quality", ["input_audio"], tags=("Audio",))
    (entry,) = briefing_evals(organization, workspace, "voice")
    assert entry["modality"] == "voice"
    assert "agent_type" not in entry and "inputs" not in entry


@pytest.mark.django_db
def test_briefing_validates_against_the_sandbox_reader(organization, workspace):
    """A copy of the sandbox validator's logic — name + modality only."""
    _template("conversation_coherence", ["conversation"], tags=("Conversation",))
    _template("audio_quality", ["input_audio"], tags=("Audio",))
    entries = {entry["name"]: entry for entry in briefing_evals(organization, workspace)}

    def accepted(name: str, run_modality: str) -> bool:
        entry = entries.get(name)
        return entry is not None and entry["modality"] in (run_modality, "any")

    assert accepted("conversation_coherence", "voice")
    assert accepted("conversation_coherence", "text")
    assert accepted("audio_quality", "voice")
    assert not accepted("audio_quality", "text")
    assert not accepted("never_offered", "voice")


def test_authored_modality_is_empty_until_a_contract_exists():
    """At launch there is no contract, so filtering on a guess is what broke a
    voice run: absence must not read as text."""
    from simulate.services.hosted_harness_gateway import _authored_modality

    class _Job:
        stage_outputs: list = []

    assert _authored_modality(_Job()) == "", "absence must not read as text"
    authored = _Job()
    authored.stage_outputs = [{"kind": "contract", "data": {"modality": "voice"}}]
    assert _authored_modality(authored) == "voice"


@pytest.mark.django_db
def test_a_catalogue_never_fails_a_launch(organization, workspace):
    from simulate.services.hosted_harness_gateway import _offered_eval_catalogue

    class _Job:
        stage_outputs: list = []
        id = "00000000-0000-0000-0000-000000000000"

    job = _Job()
    job.organization = organization
    job.workspace = workspace
    with capture_logs() as logs:
        with patch(
            "simulate.services.harness_evals.offered_evals",
            side_effect=RuntimeError("boom"),
        ):
            assert _offered_eval_catalogue(job) == []
    # The traceback noise from `logger.exception` is deliberate: a launch that
    # cannot build a catalogue must still leave a breadcrumb.
    assert any(entry["event"] == "harness_eval_catalogue_failed" for entry in logs)


# --- Binding the sandbox's picks ------------------------------------------------


@pytest.mark.django_db
def test_a_name_the_briefing_never_carried_is_refused(organization, workspace):
    from simulate.services.alk_simulate_ingestion import provision_alk_sim_run_test

    _template("customer_agent_single_choice", ["conversation"], tags=("Agents",))
    run_test, _scenarios, _agent = provision_alk_sim_run_test(
        organization,
        workspace=workspace,
        name="undefined-pick",
        personas=[{"name": "Customer", "situation": "Asks", "outcome": "Answered"}],
        modality="voice",
    )
    with pytest.raises(UnknownEvalSelection):
        create_selected_eval_configs(run_test, ["customer_agent_single_choice"], "voice")


# --- A stale pick is dropped, not refused ----------------------------------------


@pytest.mark.django_db
def test_a_pick_stale_since_launch_is_dropped_with_a_warning(organization, workspace):
    """The dispatched launch briefing is never persisted back onto the job,
    so what was actually offered at launch cannot be read back at provision
    time. A name that was a real catalog key when the briefing went out but
    has since been drafted is therefore judged by whether the tenant could
    ever have recognised it at all, not by re-checking the current offer: it
    is dropped with a `harness_eval_selection_stale` warning, and the
    provision succeeds.
    """
    from simulate.services.alk_simulate_ingestion import provision_alk_sim_run_test

    template = _template("no_misselling", ["conversation"], tags=("Conversation",))
    run_test, _scenarios, _agent = provision_alk_sim_run_test(
        organization,
        workspace=workspace,
        name="stale-pick",
        personas=[{"name": "Customer", "situation": "Asks", "outcome": "Answered"}],
        modality="voice",
    )
    template.visible_ui = False
    template.save(update_fields=["visible_ui"])

    with capture_logs() as logs:
        configs = create_selected_eval_configs(run_test, ["no_misselling"], "voice")
    assert configs == []
    assert any(
        entry["event"] == "harness_eval_selection_stale"
        and entry.get("template") == "no_misselling"
        for entry in logs
    )


@pytest.mark.django_db
def test_a_stale_custom_eval_deleted_since_launch_is_dropped_not_refused(
    organization, workspace
):
    """The other stale case: a custom eval this tenant owned, soft deleted
    before the provision runs. Still not a 400 — the tenant did once have a
    real row by this name, which is what `_tenant_known_names` looks for
    once the catalog-key check misses."""
    from simulate.services.alk_simulate_ingestion import provision_alk_sim_run_test

    template = _template(
        "my_deleted_eval",
        ["conversation"],
        tags=("Agents",),
        owner="user",
        organization=organization,
        workspace=workspace,
    )
    run_test, _scenarios, _agent = provision_alk_sim_run_test(
        organization,
        workspace=workspace,
        name="stale-deleted-pick",
        personas=[{"name": "Customer", "situation": "Asks", "outcome": "Answered"}],
        modality="voice",
    )
    from django.utils import timezone

    template.deleted = True
    template.deleted_at = timezone.now()
    template.save(update_fields=["deleted", "deleted_at"])

    with capture_logs() as logs:
        configs = create_selected_eval_configs(run_test, ["my_deleted_eval"], "voice")
    assert configs == []
    assert any(
        entry["event"] == "harness_eval_selection_stale"
        and entry.get("template") == "my_deleted_eval"
        for entry in logs
    )


@pytest.mark.django_db
def test_a_name_never_known_to_the_tenant_is_still_a_400(organization, workspace):
    """A name that is not a catalog key and was never this tenant's own
    template fails the whole provision — staleness only ever downgrades a
    400 to a warning, it never widens what counts as known."""
    from simulate.services.alk_simulate_ingestion import provision_alk_sim_run_test

    run_test, _scenarios, _agent = provision_alk_sim_run_test(
        organization,
        workspace=workspace,
        name="never-known-pick",
        personas=[{"name": "Customer", "situation": "Asks", "outcome": "Answered"}],
        modality="voice",
    )
    with pytest.raises(UnknownEvalSelection) as raised:
        create_selected_eval_configs(run_test, ["not_a_real_eval_at_all"], "voice")
    assert raised.value.names == ["not_a_real_eval_at_all"]


@pytest.mark.django_db
def test_a_pick_tagged_for_the_other_kind_is_dropped_with_a_warning(
    organization, workspace
):
    """A name the union briefing carried, fillable for this run, but tagged
    for the other kind is dropped exactly like an unfillable pick — never
    bound — while a pick tagged for the run's own kind is."""
    from simulate.services.alk_simulate_ingestion import provision_alk_sim_run_test

    _template("toxicity", ["output"], tags=("Chatbot behaviors",))
    _template("conversation_coherence", ["conversation"], tags=("Agents",))
    run_test, _scenarios, _agent = provision_alk_sim_run_test(
        organization,
        workspace=workspace,
        name="wrong-kind-pick",
        personas=[{"name": "Customer", "situation": "Asks", "outcome": "Answered"}],
        modality="voice",
    )
    with capture_logs() as logs:
        configs = create_selected_eval_configs(
            run_test, ["toxicity", "conversation_coherence"], "voice"
        )
    assert {config.eval_template.name for config in configs} == {
        "conversation_coherence"
    }
    assert any(
        entry["event"] == "harness_eval_selection_wrong_kind"
        and entry.get("template") == "toxicity"
        for entry in logs
    )


@pytest.mark.django_db
def test_a_briefed_pick_this_run_cannot_fill_is_dropped_not_refused(
    organization, workspace
):
    """The briefing is the union, so a name offered on voice reaches a text
    run. `Conversation` is relevant to both kinds, so the wrong-kind gate
    lets it through and the missing source is what drops it — with a
    warning, never a 400 for the whole provision."""
    from simulate.services.alk_simulate_ingestion import provision_alk_sim_run_test

    _template("audio_quality", ["input_audio"], tags=("Conversation",))
    _template("conversation_coherence", ["conversation"], tags=("Conversation",))
    run_test, _scenarios, _agent = provision_alk_sim_run_test(
        organization,
        workspace=workspace,
        name="unfillable-pick",
        personas=[{"name": "Customer", "situation": "Asks", "outcome": "Answered"}],
        modality="text",
    )
    with capture_logs() as logs:
        configs = create_selected_eval_configs(
            run_test, ["audio_quality", "conversation_coherence"], "text"
        )
    assert {config.eval_template.name for config in configs} == {
        "conversation_coherence"
    }
    assert any(
        entry["event"] == "harness_eval_selection_unmappable"
        and entry.get("template") == "audio_quality"
        for entry in logs
    )


@pytest.mark.django_db
def test_selecting_another_tenants_template_is_refused(organization, workspace):
    """Multi-tenancy: an invisible name must not resolve to that tenant's
    template and must not be silently dropped either."""
    from accounts.models import Organization
    from simulate.services.alk_simulate_ingestion import provision_alk_sim_run_test

    other = Organization.objects.create(name="other-tenant-selection")
    _template(
        "customer_agent_private",
        ["conversation"],
        tags=("Agents",),
        owner="user",
        organization=other,
    )
    run_test, _scenarios, _agent = provision_alk_sim_run_test(
        organization,
        workspace=workspace,
        name="tenancy",
        personas=[{"name": "Customer", "situation": "Asks", "outcome": "Answered"}],
        modality="voice",
    )
    with pytest.raises(UnknownEvalSelection) as raised:
        create_selected_eval_configs(run_test, ["customer_agent_private"], "voice")
    assert raised.value.names == ["customer_agent_private"]
    assert not SimulateEvalConfig.objects.filter(run_test=run_test).exists()


@pytest.mark.django_db
def test_selection_is_capped_and_idempotent(organization, workspace):
    from simulate.services.alk_simulate_ingestion import provision_alk_sim_run_test

    names = [f"customer_agent_pick_{index}" for index in range(MOST_SELECTED_EVALS + 3)]
    for name in names:
        _template(name, ["conversation"], tags=("Agents",))
    run_test, _scenarios, _agent = provision_alk_sim_run_test(
        organization,
        workspace=workspace,
        name="capped",
        personas=[{"name": "Customer", "situation": "Asks", "outcome": "Answered"}],
        modality="voice",
    )
    # Synthetic names, so the catalog key list is stubbed: this test is about
    # the cap, not about which names the catalog lists.
    with patch(
        "simulate.services.harness_evals.offerable_eval_names",
        return_value=frozenset(names),
    ):
        with capture_logs() as logs:
            first = create_selected_eval_configs(run_test, names, "voice")
        assert len(first) == MOST_SELECTED_EVALS
        assert all(config.error_localizer for config in first)
        # Defaults apply at creation; reprovisioning must preserve an opt-out.
        first[0].error_localizer = False
        first[0].save(update_fields=["error_localizer"])
        again = create_selected_eval_configs(run_test, names, "voice")
        assert {config.id for config in again} == {config.id for config in first}
        assert next(config for config in again if config.id == first[0].id).error_localizer is False
    assert (
        SimulateEvalConfig.objects.filter(run_test=run_test).count()
        == MOST_SELECTED_EVALS
    )
    assert any(entry["event"] == "harness_eval_selection_capped" for entry in logs)


@pytest.mark.django_db
def test_only_mapped_configs_are_runnable(organization, workspace):
    """A harness result column carries an empty mapping on purpose; running it
    would feed the evaluator nothing."""
    from simulate.services.alk_simulate_ingestion import (
        _get_or_create_harness_eval_config,
        provision_alk_sim_run_test,
    )

    selected = _template(
        "customer_agent_context_retention", ["conversation"], tags=("Agents",)
    )
    column = _template("harness_column_eval", ["conversation"], tags=("Agents",))
    run_test, _scenarios, _agent = provision_alk_sim_run_test(
        organization,
        workspace=workspace,
        name="runnable",
        personas=[{"name": "Customer", "situation": "Asks", "outcome": "Answered"}],
        modality="voice",
    )
    create_selected_eval_configs(run_test, [selected.name], "voice")
    column_config = _get_or_create_harness_eval_config(run_test, column, "booking_created")
    assert column_config.error_localizer is True

    runnable = runnable_eval_config_ids(run_test.id)
    assert len(runnable) == 1
    assert SimulateEvalConfig.objects.get(id=runnable[0]).eval_template_id == selected.id


@pytest.mark.django_db
def test_a_template_with_no_model_still_gets_one(organization, workspace):
    """Agent-type templates name no model, and the evaluator's default cannot
    consume audio, so a bound recording reached the judge as a link and every
    harness-created eval scored 0.0."""
    from simulate.services.alk_simulate_ingestion import provision_alk_sim_run_test
    from simulate.services.harness_evals import FALLBACK_EVAL_MODEL

    _template(
        "customer_agent_objection_handling",
        ["conversation"],
        tags=("Agents",),
        organization=organization,
        workspace=workspace,
    )
    run_test, _scenarios, _agent = provision_alk_sim_run_test(
        organization,
        workspace=workspace,
        name="fallback-model",
        personas=[{"name": "Customer", "situation": "Objects", "outcome": "Handled"}],
        modality="voice",
    )
    configs = create_selected_eval_configs(
        run_test, ["customer_agent_objection_handling"], "voice"
    )
    assert configs, "the template is mappable, so it must produce a config"
    assert configs[0].model == FALLBACK_EVAL_MODEL


@pytest.mark.django_db
def test_provision_creates_configs_for_chosen_evals(organization, workspace):
    _template("customer_agent_human_escalation", ["conversation"], tags=("Agents",))
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="chosen-key", workspace=workspace
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    response = _provision(
        APIClient(), capability, chosen_evals=["customer_agent_human_escalation"]
    )
    assert response.status_code == 200, response.content

    job.refresh_from_db()
    configs = list(SimulateEvalConfig.objects.filter(run_test=job.run_test))
    assert len(configs) == 1
    assert configs[0].mapping == {"conversation": "voice_recording"}


@pytest.mark.django_db
def test_provision_rejects_an_eval_name_it_never_offered(organization, workspace):
    job, _ = create_hosted_job(
        organization,
        _payload(),
        idempotency_key="unknown-eval-key",
        workspace=workspace,
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    response = _provision(APIClient(), capability, chosen_evals=["not_a_real_eval"])
    assert response.status_code == 400, response.content
    assert response.json()["error"] == "eval_selection_unknown"


@pytest.mark.django_db
def test_provision_with_one_valid_and_one_never_known_name_400s_and_binds_nothing(
    organization, workspace
):
    """A mixed batch — one good name alongside one the tenant never had —
    must still fail the whole provision, not bind the valid one and merely
    drop the other.
    """
    _template("customer_agent_human_escalation", ["conversation"], tags=("Agents",))
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="mixed-key", workspace=workspace
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    response = _provision(
        APIClient(),
        capability,
        chosen_evals=["customer_agent_human_escalation", "not_a_real_eval"],
    )
    assert response.status_code == 400, response.content
    assert response.json()["error"] == "eval_selection_unknown"
    # `_select_platform_evals` runs inside `provision_scenarios`'s own
    # `transaction.atomic()` block, which also assigns `job.run_test`, so the
    # whole provision — the eval binding *and* the run-test link — rolls
    # back together; `job.run_test_id` is therefore still unset rather than
    # pointing at a partially-bound run.
    job.refresh_from_db()
    assert job.run_test_id is None
    assert not SimulateEvalConfig.objects.filter(
        name="customer_agent_human_escalation"
    ).exists(), "the valid pick must not have been bound if the whole provision failed"


@pytest.mark.django_db
def test_provision_records_the_agent_prompt_and_creates_a_version(
    organization, workspace
):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="prompt-key", workspace=workspace
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    response = _provision(
        APIClient(), capability, agent_prompt="You are a refunds agent. Be brief."
    )
    assert response.status_code == 200, response.content

    job.refresh_from_db()
    agent = job.run_test.agent_definition
    assert agent.description == "You are a refunds agent. Be brief."
    version = agent.latest_version
    assert version is not None
    assert version.configuration_snapshot["description"] == (
        "You are a refunds agent. Be brief."
    )


@pytest.mark.django_db
def test_provision_falls_back_to_the_authored_contract_excerpt(organization, workspace):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="excerpt-key", workspace=workspace
    )
    job.stage_outputs = [
        {
            "kind": "contract",
            "data": {
                "modality": "voice",
                "agent": "uber_voice_agent",
                "call_direction": "inbound",
                "system_prompt_excerpt": "Booked rides only.",
            },
        }
    ]
    job.save(update_fields=["stage_outputs"])
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    response = _provision(APIClient(), capability)
    assert response.status_code == 200, response.content

    job.refresh_from_db()
    agent = job.run_test.agent_definition
    assert agent.description == "Booked rides only."
    # Base derives human-readable names, so the snake_case value arrives title-cased.
    assert agent.agent_name == "Uber Voice Agent"
    assert agent.inbound is True


@pytest.mark.django_db
def test_provision_records_explicit_rl_call_behavior_over_authored_direction(
    organization, workspace
):
    payload = _payload()
    payload["agent"] = {
        **payload["agent"],
        "config": {"inbound": False, "target_speaks_first": True},
    }
    job, _ = create_hosted_job(
        organization,
        payload,
        idempotency_key="explicit-call-behavior",
        workspace=workspace,
    )
    job.stage_outputs = [
        {
            "kind": "contract",
            "data": {
                "modality": "voice",
                "call_direction": "inbound",
                "system_prompt_excerpt": "Book rides safely.",
            },
        }
    ]
    job.save(update_fields=["stage_outputs"])
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")

    response = _provision(APIClient(), capability)
    assert response.status_code == 200, response.content

    job.refresh_from_db()
    agent = job.run_test.agent_definition
    assert agent.inbound is False
    assert agent.target_speaks_first is True
    assert agent.latest_version.configuration_snapshot["inbound"] is False
    assert agent.latest_version.configuration_snapshot["target_speaks_first"] is True


# --- The hand list is gone --------------------------------------------------------


def test_the_json_manifest_is_gone_and_nothing_imports_it():
    """Nobody adds a hand list of eval names anywhere in `simulate/`."""
    here = Path(__file__).resolve()
    root = here.parents[2]
    assert not (root / "simulate" / "data" / "harness_evals.json").exists()
    offenders = []
    for path in (root / "simulate").rglob("*.py"):
        if path == here:
            continue
        text = path.read_text(encoding="utf-8")
        if "harness_evals.json" in text or "harness_evals_manifest" in text:
            offenders.append(str(path.relative_to(root)))
    assert offenders == [], offenders


@pytest.mark.django_db
def test_a_wrong_kind_pick_never_displaces_a_fillable_one(organization, workspace):
    """The wrong-kind drop happens before the `[:MOST_SELECTED_EVALS]` slice,
    so a voice run picking a chat-only name alongside eight fillable ones
    still binds all eight — the wrong-kind pick took no slot.
    """
    from simulate.services.alk_simulate_ingestion import provision_alk_sim_run_test

    assert len(CAP_FILLERS) == MOST_SELECTED_EVALS, "the cap must be filled exactly"
    _template("toxicity", ["output"], tags=("Chatbot behaviors",))
    for name in CAP_FILLERS:
        _template(name, ["conversation"], tags=("Conversation",))
    run_test, _scenarios, _agent = provision_alk_sim_run_test(
        organization,
        workspace=workspace,
        name="wrong-kind-took-no-slot",
        personas=[{"name": "Customer", "situation": "Asks", "outcome": "Answered"}],
        modality="voice",
    )
    with capture_logs() as logs:
        configs = create_selected_eval_configs(
            run_test, ["toxicity", *CAP_FILLERS], "voice"
        )
    assert {config.eval_template.name for config in configs} == set(CAP_FILLERS)
    assert len(configs) == MOST_SELECTED_EVALS
    events = [entry["event"] for entry in logs]
    assert "harness_eval_selection_wrong_kind" in events
    assert "harness_eval_selection_capped" not in events


@pytest.mark.django_db
def test_an_unfillable_pick_never_displaces_a_fillable_one(organization, workspace):
    """An unfillable pick is dropped before the cap slice too, the same as a
    wrong-kind one, so a text run picking one unfillable name alongside
    eight fillable ones still binds all eight.
    """
    from simulate.services.alk_simulate_ingestion import provision_alk_sim_run_test

    assert len(CAP_FILLERS) == MOST_SELECTED_EVALS, "the cap must be filled exactly"
    _template("audio_quality", ["input_audio"], tags=("Conversation",))
    for name in CAP_FILLERS:
        _template(name, ["conversation"], tags=("Conversation",))
    run_test, _scenarios, _agent = provision_alk_sim_run_test(
        organization,
        workspace=workspace,
        name="unfillable-took-no-slot",
        personas=[{"name": "Customer", "situation": "Asks", "outcome": "Answered"}],
        modality="text",
    )
    with capture_logs() as logs:
        configs = create_selected_eval_configs(
            run_test, ["audio_quality", *CAP_FILLERS], "text"
        )
    assert {config.eval_template.name for config in configs} == set(CAP_FILLERS)
    assert len(configs) == MOST_SELECTED_EVALS
    events = [entry["event"] for entry in logs]
    assert "harness_eval_selection_unmappable" in events
    assert "harness_eval_selection_capped" not in events


@pytest.mark.django_db
def test_the_cap_only_drops_survivors_past_the_eighth(organization, workspace):
    """Nine fillable, same-kind picks: nothing is dropped for kind or mapping,
    so the cap is the only gate left, and it names exactly the ninth."""
    from simulate.services.alk_simulate_ingestion import provision_alk_sim_run_test

    assert len(CAP_FILLERS) == MOST_SELECTED_EVALS, "the cap must be filled exactly"
    for name in (*CAP_FILLERS, ONE_TOO_MANY):
        _template(name, ["conversation"], tags=("Conversation",))
    run_test, _scenarios, _agent = provision_alk_sim_run_test(
        organization,
        workspace=workspace,
        name="nine-fillable",
        personas=[{"name": "Customer", "situation": "Asks", "outcome": "Answered"}],
        modality="voice",
    )
    with capture_logs() as logs:
        configs = create_selected_eval_configs(
            run_test, [*CAP_FILLERS, ONE_TOO_MANY], "voice"
        )
    assert {config.eval_template.name for config in configs} == set(CAP_FILLERS)
    assert len(configs) == MOST_SELECTED_EVALS
    capped = next(
        entry for entry in logs if entry["event"] == "harness_eval_selection_capped"
    )
    assert capped["dropped"] == [ONE_TOO_MANY]


# --- TH-8055: the tool-call judge switch is independent of the catalog ------


@pytest.mark.django_db
def test_receipt_dispatches_evaluations_when_only_the_tool_switch_is_on(
    organization, django_capture_on_commit_callbacks
):
    """A hosted receipt for an environment with zero selected evals must
    still dispatch when the run test's tool-call judge is on."""
    from simulate.services.hosted_harness import canonical_digest
    from simulate.services.hosted_harness_ingestion import ingest_result_receipt

    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="tool-switch-only-key"
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client = APIClient()
    provision = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Tool switch only",
            "modality": "text",
            "personas": [
                {
                    "scenario_key": "tool-switch",
                    "name": "Caller",
                    "situation": "Needs help",
                    "outcome": "Receives help",
                }
            ],
        },
        format="json",
        **_headers(capability),
    )
    assert provision.status_code == 200, provision.content
    provisioned = provision.json()["result"]
    scenario = provisioned["scenarios"][0]

    job.refresh_from_db()
    job.run_test.enable_tool_evaluation = True
    job.run_test.save(update_fields=["enable_tool_evaluation"])

    client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "begin",
            "run_test_id": provisioned["run_test_id"],
            "scenario_keys": ["tool-switch"],
        },
        format="json",
        **_headers(capability),
    )
    receipt = {
        "schema_version": "futureagi.harness-result.v1",
        "job_id": str(job.id),
        "attempt_id": str(capability.attempt.id),
        "attempt_number": 1,
        "scenario_key": "tool-switch",
        "scenario_id": scenario["scenario_id"],
        "scenario_attempt": 1,
        "world_index": None,
        "status": "passed",
        "sub_goals": [],
        "evaluations": [],
        "call": None,
        "failure": None,
    }
    receipt["digest"] = canonical_digest(receipt)

    with (
        patch(
            "simulate.services.alk_simulate_ingestion._dispatch_evaluations_once"
        ) as dispatch,
        django_capture_on_commit_callbacks(execute=True),
    ):
        _, created = ingest_result_receipt(capability.attempt, receipt)

    assert created is True
    dispatch.assert_called_once()


@pytest.mark.django_db
def test_hosted_scenario_provision_leaves_tool_evaluation_off(organization, workspace):
    """The v3/hosted gateway is not a door for the tool-call switch:
    `provision_scenarios` never reads `enable_tool_evaluation` off the guest
    payload, so even a guest payload that carries the key cannot turn it on."""
    from simulate.services.hosted_harness import provision_scenarios

    job, _ = create_hosted_job(
        organization,
        _payload(),
        idempotency_key="tool-eval-guest-key",
        workspace=workspace,
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    payload = {
        "name": "Guest tries the switch",
        "modality": "voice",
        "enable_tool_evaluation": True,
        "personas": [
            {
                "scenario_key": "refund-request",
                "name": "Customer",
                "situation": "Asks for a refund",
                "outcome": "Agent follows policy",
            }
        ],
    }
    provision_scenarios(capability.attempt, payload)

    job.refresh_from_db()
    assert job.run_test_id is not None
    assert job.run_test.enable_tool_evaluation is False


@pytest.mark.django_db
def test_the_tool_switch_does_not_regrade_a_harness_result_column(
    organization, django_capture_on_commit_callbacks
):
    """The switch must not widen an explicit empty selection into "every
    config on the run test" -- that would re-grade, and on error overwrite,
    a harness result column's own stored verdict."""
    from model_hub.models.evals_metric import EvalTemplate
    from simulate.models import CallExecution, SimulateEvalConfig
    from simulate.services.hosted_harness import canonical_digest
    from simulate.services.hosted_harness_ingestion import ingest_result_receipt

    EvalTemplate.objects.create(
        name="Politeness",
        description="Politeness description",
        config={"required_keys": []},
        eval_tags=["Conversation"],
        eval_id=0,
    )

    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="tool-switch-no-regrade-key"
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client = APIClient()
    provision = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Tool switch no regrade",
            "modality": "text",
            "personas": [
                {
                    "scenario_key": "tool-switch-regrade",
                    "name": "Caller",
                    "situation": "Needs help",
                    "outcome": "Receives help",
                }
            ],
        },
        format="json",
        **_headers(capability),
    )
    assert provision.status_code == 200, provision.content
    provisioned = provision.json()["result"]
    scenario = provisioned["scenarios"][0]

    job.refresh_from_db()
    job.run_test.enable_tool_evaluation = True
    job.run_test.save(update_fields=["enable_tool_evaluation"])

    client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "begin",
            "run_test_id": provisioned["run_test_id"],
            "scenario_keys": ["tool-switch-regrade"],
        },
        format="json",
        **_headers(capability),
    )
    receipt = {
        "schema_version": "futureagi.harness-result.v1",
        "job_id": str(job.id),
        "attempt_id": str(capability.attempt.id),
        "attempt_number": 1,
        "scenario_key": "tool-switch-regrade",
        "scenario_id": scenario["scenario_id"],
        "scenario_attempt": 1,
        "world_index": None,
        "status": "passed",
        "sub_goals": [],
        "evaluations": [
            {
                "name": "Politeness",
                "kind": "eval",
                "platform_template": "Politeness",
                "passed": True,
                "reason": "The agent was polite throughout.",
            }
        ],
        "call": None,
        "failure": None,
    }
    receipt["digest"] = canonical_digest(receipt)

    # Patched at the call site `_dispatch_evaluations_once` actually uses.
    with (
        patch(
            "simulate.services.alk_simulate_ingestion._run_simulate_evaluations_task"
        ) as task,
        django_capture_on_commit_callbacks(execute=True),
    ):
        _, created = ingest_result_receipt(capability.attempt, receipt)

    assert created is True
    call = CallExecution.objects.get(
        hosted_registration__scenario_key="tool-switch-regrade"
    )
    config = SimulateEvalConfig.objects.get(
        run_test_id=job.run_test_id, name="Politeness"
    )
    # [] -- not None -- is what stops the dispatch from widening to "every
    # config on the run test".
    task.apply_async.assert_called_once_with(args=(str(call.id), []))

    # The harness's own verdict on that column is untouched: the mocked task
    # never ran, so nothing re-graded or overwrote it.
    call.refresh_from_db()
    assert call.eval_outputs[str(config.id)]["source"] == "harness"


@pytest.mark.django_db
def test_a_switch_lookup_failure_never_loses_the_receipt(
    organization, django_capture_on_commit_callbacks
):
    """A transient DB error on the tool-switch lookup must not propagate out
    of `_apply_receipt_to_call` and lose the receipt."""
    from django.db.utils import OperationalError

    from simulate.models import CallExecution
    from simulate.services.hosted_harness import canonical_digest
    from simulate.services.hosted_harness_ingestion import ingest_result_receipt

    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="tool-switch-lookup-failure-key"
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client = APIClient()
    provision = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Tool switch lookup failure",
            "modality": "text",
            "personas": [
                {
                    "scenario_key": "tool-switch-lookup-failure",
                    "name": "Caller",
                    "situation": "Needs help",
                    "outcome": "Receives help",
                }
            ],
        },
        format="json",
        **_headers(capability),
    )
    assert provision.status_code == 200, provision.content
    provisioned = provision.json()["result"]
    scenario = provisioned["scenarios"][0]

    client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "begin",
            "run_test_id": provisioned["run_test_id"],
            "scenario_keys": ["tool-switch-lookup-failure"],
        },
        format="json",
        **_headers(capability),
    )
    receipt = {
        "schema_version": "futureagi.harness-result.v1",
        "job_id": str(job.id),
        "attempt_id": str(capability.attempt.id),
        "attempt_number": 1,
        "scenario_key": "tool-switch-lookup-failure",
        "scenario_id": scenario["scenario_id"],
        "scenario_attempt": 1,
        "world_index": None,
        "status": "passed",
        "sub_goals": [],
        "evaluations": [],
        "call": None,
        "failure": None,
    }
    receipt["digest"] = canonical_digest(receipt)

    with (
        patch(
            "simulate.services.hosted_harness_ingestion._tool_evaluation_on",
            side_effect=OperationalError("connection reset"),
        ),
        django_capture_on_commit_callbacks(execute=True),
    ):
        _, created = ingest_result_receipt(capability.attempt, receipt)

    assert created is True
    call = CallExecution.objects.get(
        hosted_registration__scenario_key="tool-switch-lookup-failure"
    )
    assert call.status == CallExecution.CallStatus.COMPLETED
