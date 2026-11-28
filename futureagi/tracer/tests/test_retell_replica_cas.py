import pytest
from django.db import connection

from tfc.tests.lagging_replica import lagging_replica  # noqa: F401
from tracer.models.observability_provider import ObservabilityProvider, ProviderChoices
from tracer.utils.observability_provider import _write_retell_state


@pytest.fixture
def provider(observe_project):
    return ObservabilityProvider.objects.create(
        project=observe_project,
        organization_id=observe_project.organization_id,
        workspace_id=observe_project.workspace_id,
        provider=ProviderChoices.RETELL,
    )


@pytest.mark.django_db(transaction=True)
def test_consecutive_checkpoints_with_a_lagging_replica(
    lagging_replica,  # noqa: F811
    provider,
    settings,
):
    settings.READ_REPLICA_OPT_IN = ["ObservabilityProvider"]
    lagging_replica.freeze()
    rows = ObservabilityProvider.all_objects.filter(pk=provider.pk)
    sibling = {"configuration_health": {"failure_count": 1}}
    rows.using("default").update(poll_state=sibling)

    for cursor in ("page-two", "page-three"):
        assert rows.values_list("poll_state", flat=True).get() == {}
        checkpoint = {"window": {"key": cursor}}
        assert _write_retell_state(provider.pk, checkpoint)
        provider.refresh_from_db(using="default")
        assert provider.poll_state == {**sibling, "retell": checkpoint}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("namespace", "edits", "should_save"),
    [
        ("configuration_health", 1, True),
        ("retell", 1, False),
        ("configuration_health", 100, False),
    ],
    ids=["merge-sibling", "competing-checkpoint", "persistent-contention"],
)
def test_intervening_poll_state_write(provider, namespace, edits, should_save):
    rows = ObservabilityProvider.all_objects.filter(pk=provider.pk)
    revision = 0

    def edit_after_read(execute, sql, params, many, context):
        nonlocal revision
        result = execute(sql, params, many, context)
        # The SELECT has its result; change the real row before the checkpoint
        # writer can use that snapshot. No thread timing or ORM mocks needed.
        if (
            sql.startswith("SELECT")
            and ObservabilityProvider._meta.db_table in sql
            and revision < edits
        ):
            revision += 1
            rows.update(poll_state={namespace: {"revision": revision}})
        return result

    checkpoint = {"window": {"key": "next-page"}}
    with connection.execute_wrapper(edit_after_read):
        assert _write_retell_state(provider.pk, checkpoint) is should_save

    provider.refresh_from_db()
    assert revision > 0
    expected = {namespace: {"revision": revision}}
    if should_save:
        expected["retell"] = checkpoint
    assert provider.poll_state == expected
