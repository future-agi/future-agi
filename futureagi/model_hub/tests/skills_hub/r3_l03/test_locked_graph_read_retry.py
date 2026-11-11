"""A read racing a dependency apply renews its row set; a write still fails CAS."""

import pytest

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v12 import authoring
from model_hub.services.skills_hub.v12.authoring import locked_context_graph

pytestmark = pytest.mark.django_db(transaction=True)


class _Stale:
    resolved_lock = {"links": []}


def _stale_first_read(monkeypatch):
    real = authoring._owned
    calls = {"n": 0}

    def owned(principal, context):
        calls["n"] += 1
        # The first (preliminary) read predates the committed apply.
        return _Stale() if calls["n"] == 1 else real(principal, context)

    monkeypatch.setattr(authoring, "_owned", owned)
    return calls


def test_reader_racing_dependency_apply_sees_committed_draft_not_conflict(
    principal, context, hub_skill, publish_skill, accept_links, monkeypatch
):
    publish_skill(hub_skill, labels=["production"])
    accept_links(
        context,
        [{"skill_id": str(hub_skill.pk), "selector": {"label": "production"}}],
    )
    calls = _stale_first_read(monkeypatch)
    with locked_context_graph(
        principal=principal, context_id=context.pk, write=False
    ) as (_, draft, resources):
        assert hub_skill.pk in resources
        assert len(draft.resolved_lock["links"]) == 1
    assert calls["n"] >= 3


def test_writer_racing_dependency_apply_still_fails_compare_and_swap(
    principal, context, hub_skill, publish_skill, accept_links, monkeypatch
):
    publish_skill(hub_skill, labels=["production"])
    accept_links(
        context,
        [{"skill_id": str(hub_skill.pk), "selector": {"label": "production"}}],
    )
    _stale_first_read(monkeypatch)
    with pytest.raises(SkillsHubError) as error:
        with locked_context_graph(
            principal=principal, context_id=context.pk, write=True
        ):
            pytest.fail("A stale write must not proceed")
    assert error.value.code == "draft_conflict" and error.value.status == 409


def test_read_inside_a_callers_transaction_does_not_retry_and_invert_lock_order(
    principal, context, hub_skill, publish_skill, accept_links, monkeypatch
):
    from django.db import transaction

    publish_skill(hub_skill, labels=["production"])
    accept_links(
        context,
        [{"skill_id": str(hub_skill.pk), "selector": {"label": "production"}}],
    )
    _stale_first_read(monkeypatch)
    with transaction.atomic():
        with pytest.raises(SkillsHubError) as error:
            with locked_context_graph(
                principal=principal, context_id=context.pk, write=False
            ):
                pytest.fail("A nested read must not relock children after its parent")
    assert error.value.code == "draft_conflict"
