"""Persisted intent must recover transport failures without duplicating demo data."""

from unittest.mock import patch

import pytest
from django.db import transaction

from accounts.demo_seeding import (
    publish_pending_demo_trace,
    recover_pending_demo_data,
    seed_pending_demo_data,
)
from accounts.models.demo_seed import DemoSeedRequest, DemoTracePublication
from accounts.utils import first_signup
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace


@pytest.fixture
def signup(monkeypatch):
    monkeypatch.setenv("CH25_TRACE_DUAL_WRITE", "true")
    with (
        patch("accounts.utils.is_cloud_env", return_value=False),
        patch("accounts.utils._fire_deployment_telemetry_registration"),
        patch("accounts.utils._record_signup_api_key_event"),
        patch(
            "tfc.temporal.drop_in.start_activity",
            side_effect=TimeoutError("dispatch timed out"),
        ),
    ):
        yield lambda **kwargs: first_signup(
            {
                "email": "recovery@futureagi.com",
                "full_name": "Recovery Test",
                "password": "SafePassword123!",
            },
            **kwargs,
        )


def identities(user):
    return set(
        Trace.objects.filter(project__organization_id=user.organization_id).values_list(
            "id", flat=True
        )
    )


@pytest.mark.django_db(transaction=True)
def test_lost_initial_dispatch_is_recovered_without_duplicate_traces(signup):
    user = signup()
    intent = DemoSeedRequest.objects.get(user=user)
    assert intent.completed_at is None
    assert not identities(user)
    with patch("tfc.temporal.drop_in.start_activity") as dispatch:
        recover_pending_demo_data()
    call = dispatch.call_args
    assert call.args == ("seed_demo_data_activity",)
    assert call.kwargs["args"] == (str(user.organization_id),)
    assert call.kwargs["task_id"] == f"seed-demo-data-{user.organization_id}"
    assert call.kwargs["dispatch_timeout_seconds"] == 2
    with patch("accounts.user_onboard.upload_demo_dataset") as datasets:
        seed_pending_demo_data(user.organization_id)
        first_ids = identities(user)
        seed_pending_demo_data(user.organization_id)
        datasets.assert_called_once()
    assert len(first_ids) == 2
    assert identities(user) == first_ids
    assert ObservationSpan.objects.filter(trace_id__in=first_ids).count() == 70
    intent.refresh_from_db()
    assert intent.completed_at is not None
    # Both lost publication starts survive even though the seed stage completed.
    assert (
        DemoTracePublication.objects.filter(
            trace_id__in=first_ids, completed_at__isnull=True
        ).count()
        == 2
    )
    with patch("tfc.temporal.drop_in.start_activity") as dispatch:
        recover_pending_demo_data()
    assert {c.kwargs["args"][0] for c in dispatch.call_args_list} == {
        str(i) for i in first_ids
    }
    with patch("accounts.user_onboard._write_demo_trace_to_clickhouse") as publish:
        for trace_id in first_ids:
            publish_pending_demo_trace(trace_id)
            publish_pending_demo_trace(trace_id)
        assert publish.call_count == 2
    with patch("tfc.temporal.drop_in.start_activity") as dispatch:
        recover_pending_demo_data()
        dispatch.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_mid_seed_crash_rolls_back_before_retry(signup):
    from accounts.user_onboard import create_observation_spans

    user = signup()
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("second project failed")
        return create_observation_spans(*args, **kwargs)

    with patch(
        "accounts.user_onboard.create_observation_spans", side_effect=fail_second
    ):
        with pytest.raises(RuntimeError, match="second project failed"):
            seed_pending_demo_data(user.organization_id)
    assert not Project.objects.filter(organization_id=user.organization_id).exists()
    assert not DemoTracePublication.objects.exists()
    with patch("accounts.user_onboard.upload_demo_dataset"):
        seed_pending_demo_data(user.organization_id)
    assert len(identities(user)) == 2


@pytest.mark.django_db(transaction=True)
def test_dataset_failure_does_not_repeat_committed_traces(signup):
    user = signup()
    with patch(
        "accounts.user_onboard.upload_demo_dataset",
        side_effect=RuntimeError("dataset failed"),
    ):
        with pytest.raises(RuntimeError, match="dataset failed"):
            seed_pending_demo_data(user.organization_id)
    before = identities(user)
    assert len(before) == 2
    with patch("accounts.user_onboard.upload_demo_dataset"):
        seed_pending_demo_data(user.organization_id)
    assert identities(user) == before


@pytest.mark.django_db(transaction=True)
def test_clickhouse_failure_keeps_publication_pending(signup):
    user = signup()
    with patch("accounts.user_onboard.upload_demo_dataset"):
        seed_pending_demo_data(user.organization_id)
    trace_id = next(iter(identities(user)))
    with patch(
        "accounts.user_onboard._write_demo_trace_to_clickhouse",
        side_effect=RuntimeError("CH down"),
    ):
        with pytest.raises(RuntimeError, match="CH down"):
            publish_pending_demo_trace(trace_id)
    assert DemoTracePublication.objects.get(pk=trace_id).completed_at is None
    with patch("accounts.user_onboard._write_demo_trace_to_clickhouse") as write:
        publish_pending_demo_trace(trace_id)
    assert write.call_args.args[0].id == trace_id
    assert len(write.call_args.args[1]) == 35


@pytest.mark.django_db(transaction=True)
def test_rollback_removes_account_intent_and_dispatch(signup):
    with patch("accounts.utils.process_post_registration") as dispatch:
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                user = signup()
                raise RuntimeError("rollback")
        assert not DemoSeedRequest.objects.filter(user_id=user.id).exists()
        dispatch.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_clean_actor_opt_out_has_no_pending_work(signup):
    with patch("accounts.utils.process_post_registration") as dispatch:
        user = signup(seed_demo_data=False)
        dispatch.assert_not_called()
    assert not DemoSeedRequest.objects.filter(user=user).exists()


def test_recovery_schedule_is_registered():
    from tfc.temporal.schedules import ALL_SCHEDULES

    schedule = next(s for s in ALL_SCHEDULES if s.schedule_id == "recover-demo-seeding")
    assert schedule.activity_name == "recover_demo_data_activity"
    assert schedule.interval_seconds == 60


@pytest.mark.django_db(transaction=True)
def test_concurrent_delivery_creates_each_stage_once(signup):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections

    user = signup()
    barrier = Barrier(2)

    def deliver():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            seed_pending_demo_data(user.organization_id)
        finally:
            close_old_connections()

    with patch("accounts.user_onboard.upload_demo_dataset") as datasets:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(deliver) for _ in range(2)]
            for future in futures:
                future.result(timeout=30)
        datasets.assert_called_once()
    assert len(identities(user)) == 2
    assert DemoTracePublication.objects.count() == 2


@pytest.mark.django_db(transaction=True)
def test_marketplace_dispatch_persists_intent_and_pins_organization(signup):
    from accounts.utils import process_post_registration

    user = signup(seed_demo_data=False)
    with pytest.raises(TimeoutError):
        process_post_registration(user.id, None)
    assert (
        DemoSeedRequest.objects.get(organization_id=user.organization_id).user_id
        == user.id
    )


@pytest.mark.django_db(transaction=True)
def test_one_owner_can_seed_two_organizations(signup):
    from accounts.models import Organization
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.utils import process_post_registration

    user = signup()
    second = Organization.objects.create(name="Second org")
    OrganizationMembership.objects.create(
        user=user, organization=second, role="Owner", level=70, is_active=True
    )
    with pytest.raises(TimeoutError):
        process_post_registration(user.id, None, organization_id=second.id)
    with patch("accounts.user_onboard.upload_demo_dataset"):
        seed_pending_demo_data(user.organization_id)
        seed_pending_demo_data(second.id)
    assert Project.objects.filter(organization_id=second.id).count() == 2
    assert len(identities(user)) == 2
    assert (
        DemoSeedRequest.objects.filter(user=user, completed_at__isnull=False).count()
        == 2
    )


@pytest.mark.django_db(transaction=True)
def test_recovery_continues_after_one_dispatch_fails(signup):
    user = signup()
    with patch("accounts.user_onboard.upload_demo_dataset"):
        seed_pending_demo_data(user.organization_id)
    with patch(
        "tfc.temporal.drop_in.start_activity",
        side_effect=[TimeoutError("lost ack"), "accepted"],
    ) as start:
        recover_pending_demo_data()
    assert start.call_count == 2
    first_ids = {c.kwargs["task_id"] for c in start.call_args_list}
    with patch("tfc.temporal.drop_in.start_activity") as start:
        recover_pending_demo_data()
    assert {c.kwargs["task_id"] for c in start.call_args_list} == first_ids


@pytest.mark.django_db(transaction=True)
def test_legacy_reseed_works_before_publication_table_exists(signup):
    from importlib import import_module

    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    from accounts.user_onboard import create_demo_traces_and_spans

    user = signup(seed_demo_data=False)
    old = ("accounts", "0028_merge_authtoken_scope_and_simulation_agent_key")
    new = ("accounts", "0030_queue_existing_demo_trace_publications")
    executor = MigrationExecutor(connection)
    try:
        executor.migrate([old])
        assert (
            "accounts_demotracepublication"
            not in connection.introspection.table_names()
        )
        # 0020 explicitly uses this historical-compatible seeding path.
        assert (
            create_demo_traces_and_spans(str(user.organization_id), publish=False)
            is None
        )
        trace_ids = identities(user)
        assert len(trace_ids) == 2
        executor = MigrationExecutor(connection)
        executor.migrate([new])
        assert (
            set(DemoTracePublication.objects.values_list("trace_id", flat=True))
            == trace_ids
        )
        # The data operation itself is idempotent.
        operation = import_module(
            "accounts.migrations.0030_queue_existing_demo_trace_publications"
        )
        with connection.schema_editor() as editor:
            operation.queue_existing_demo_traces(
                executor.loader.project_state([new]).apps, editor
            )
        assert DemoTracePublication.objects.count() == 2
    finally:
        MigrationExecutor(connection).migrate([new])


@pytest.mark.django_db(transaction=True)
def test_dataset_stage_rolls_back_partial_writes_and_retries(signup):
    from copy import deepcopy

    from accounts.base_data import prompt_config, run_prompt_config
    from model_hub.models.develop_dataset import Dataset, Row
    from model_hub.models.evals_metric import EvalTemplate

    user = signup()
    for eval_id in (101, 47, 66, 100):
        EvalTemplate.no_workspace_objects.get_or_create(
            eval_id=eval_id,
            defaults={"name": f"eval-{eval_id}", "config": {"config": {}}},
        )
    original = deepcopy((prompt_config, run_prompt_config))
    with patch(
        "accounts.user_onboard.create_image_dataset",
        side_effect=RuntimeError("image stage failed"),
    ):
        with pytest.raises(RuntimeError, match="image stage failed"):
            seed_pending_demo_data(user.organization_id)
    assert not Dataset.objects.filter(organization_id=user.organization_id).exists()
    before = identities(user)
    seed_pending_demo_data(user.organization_id)
    datasets = Dataset.objects.filter(organization_id=user.organization_id)
    assert set(datasets.values_list("name", flat=True)) == {
        "Demo-dataset",
        "Image-Demo-dataset",
    }
    assert all(Row.objects.filter(dataset=d).count() == 50 for d in datasets)
    assert identities(user) == before
    assert (prompt_config, run_prompt_config) == original


@pytest.mark.django_db(transaction=True)
def test_signup_dispatch_deadline_preserves_pending_intent(signup):
    import asyncio
    import time

    from tfc.temporal.drop_in.runner import start_activity

    async def unavailable_client():
        await asyncio.sleep(3600)

    started = time.monotonic()
    with (
        patch("tfc.temporal.drop_in.start_activity", new=start_activity),
        patch("tfc.temporal.common.client.get_client", side_effect=unavailable_client),
    ):
        user = signup()
    assert time.monotonic() - started < 5
    assert (
        DemoSeedRequest.objects.get(organization_id=user.organization_id).completed_at
        is None
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("initial_span_count", [10, 35])
def test_clickhouse_lost_ack_retries_same_rows_without_duplicates(
    signup, initial_span_count
):
    from clickhouse_connect import get_client

    from accounts.user_onboard import _write_demo_trace_to_clickhouse
    from tracer.services.clickhouse.v2 import get_v2_config

    user = signup()
    with patch("accounts.user_onboard.upload_demo_dataset"):
        seed_pending_demo_data(user.organization_id)
    trace_id = next(iter(identities(user)))

    def written_but_ack_lost(trace, spans):
        _write_demo_trace_to_clickhouse(trace, spans[:initial_span_count])
        raise RuntimeError("write acknowledged too late")

    with patch(
        "accounts.user_onboard._write_demo_trace_to_clickhouse",
        side_effect=written_but_ack_lost,
    ):
        with pytest.raises(RuntimeError, match="acknowledged too late"):
            publish_pending_demo_trace(trace_id)
    assert DemoTracePublication.objects.get(pk=trace_id).completed_at is None
    publish_pending_demo_trace(trace_id)
    trace = Trace.objects.get(pk=trace_id)
    cfg = get_v2_config()
    with get_client(
        host=cfg["host"],
        port=cfg["http_port"],
        username=cfg["user"],
        password=cfg["password"] or "",
        database=cfg["database"],
    ) as ch:
        assert ch.query(
            "SELECT count() FROM traces FINAL WHERE id = {id:UUID}",
            parameters={"id": str(trace_id)},
        ).first_row == (1,)
        assert ch.query(
            "SELECT count() FROM trace_sessions FINAL WHERE trace_session_id = {id:UUID}",
            parameters={"id": str(trace.session_id)},
        ).first_row == (1,)
        ids = ch.query(
            "SELECT id, trace_name FROM spans FINAL WHERE trace_id = {id:String}",
            parameters={"id": str(trace_id)},
        ).result_rows
        rollup = ch.query(
            "SELECT countMerge(span_count), sumMerge(total_tokens_sum), "
            "sumMerge(prompt_tokens_sum), sumMerge(completion_tokens_sum), sumMerge(cost_sum) "
            "FROM spans_per_session "
            "WHERE project_id = {project:UUID} AND trace_session_id = {session:UUID}",
            parameters={
                "project": str(trace.project_id),
                "session": str(trace.session_id),
            },
        ).first_row
        source = ch.query(
            "SELECT count(), sum(total_tokens), sum(prompt_tokens), sum(completion_tokens), sum(cost) "
            "FROM spans FINAL WHERE project_id = {project:UUID} AND trace_id = {trace:String}",
            parameters={"project": str(trace.project_id), "trace": str(trace.id)},
        ).first_row
        assert rollup[0] == 35
        assert rollup[1:] == pytest.approx(source[1:])
    assert {row[0] for row in ids} == set(
        ObservationSpan.objects.filter(trace=trace).values_list("id", flat=True)
    )
    assert len(ids) == 35
    assert {row[1] for row in ids} == {trace.name}


def test_product_urlconf_excludes_clean_actor_endpoint():
    from django.urls import Resolver404, resolve

    with pytest.raises(Resolver404):
        resolve("/accounts/e2e/clean-signup/", urlconf="tfc.urls")


@pytest.mark.django_db
def test_public_signup_cannot_opt_out_of_demo_seeding(api_client, monkeypatch):
    monkeypatch.setenv("ENV_TYPE", "local")
    response = api_client.post(
        "/accounts/signup/",
        {
            "email": "public-signup@futureagi.com",
            "full_name": "Public Signup",
            "password": "SafePassword123!",
            "seed_demo_data": False,
        },
        format="json",
    )
    assert response.status_code == 200
    # Unknown input is discarded by the public signup allowlist.
    assert DemoSeedRequest.objects.filter(
        user__email="public-signup@futureagi.com"
    ).exists()


@pytest.mark.django_db
def test_aws_signup_keeps_notifications_and_creates_seed_intent(monkeypatch):
    from accounts.aws_marketplace_utils import process_aws_signup
    from accounts.models import Organization, User
    from accounts.models.aws_marketplace import AWSMarketplaceCustomer
    from accounts.models.organization_membership import OrganizationMembership

    org = Organization.objects.create(name="AWS customer")
    customer = AWSMarketplaceCustomer.objects.create(
        customer_identifier="demo-recovery-aws",
        customer_aws_account_id="123456789012",
        product_code="test",
        organization=org,
    )
    with patch("tfc.temporal.drop_in.start_activity") as start:
        process_aws_signup(
            "aws-demo@futureagi.com", "AWS Demo", customer.customer_identifier
        )
    user = User.objects.get(email="aws-demo@futureagi.com")
    assert OrganizationMembership.objects.filter(
        user=user, organization=org, is_active=True
    ).exists()
    assert DemoSeedRequest.objects.get(organization=org).user_id == user.id
    assert start.call_args.kwargs["kwargs"] == {"organization_id": str(org.id)}
    assert start.call_args.kwargs["args"][
        1
    ]  # Generated credentials still reach the email activity.
