"""A new organization's demo seed completes; one that cannot complete stops."""

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import InterfaceError, OperationalError
from django.utils import timezone
from rest_framework.exceptions import NotFound
from structlog.testing import capture_logs

from accounts.demo_seeding import recover_pending_demo_data, seed_pending_demo_data
from accounts.models import Organization, User
from accounts.utils import first_signup

# Evaluation templates the demo dataset links its columns to. An install loads
# them with the system evaluations; the test database starts without them.
DEMO_EVAL_IDS = (101, 47, 66, 100)

# More failed deliveries than a bounded retry allows.
DELIVERIES = 10


@pytest.fixture
def signup(monkeypatch):
    monkeypatch.setenv("CH25_TRACE_DUAL_WRITE", "true")
    with (
        patch("accounts.utils.is_cloud_env", return_value=False),
        patch("accounts.utils._fire_deployment_telemetry_registration"),
        patch(
            "tfc.temporal.drop_in.start_activity",
            side_effect=TimeoutError("dispatch timed out"),
        ),
    ):
        yield lambda: first_signup(
            {
                "email": "new-signup@futureagi.com",
                "full_name": "New Signup",
                "password": "SafePassword123!",
            }
        )


def age(user, hours=24):
    """Make the signup and its organization this many hours old."""
    then = timezone.now() - timedelta(hours=hours)
    Organization.objects.filter(id=user.organization_id).update(created_at=then)
    User.objects.filter(id=user.id).update(created_at=then)


def is_dispatched(user):
    with patch("tfc.temporal.drop_in.start_activity") as dispatch:
        recover_pending_demo_data()
    return any(
        c.args == ("seed_demo_data_activity",)
        and c.kwargs["args"] == (str(user.organization_id),)
        for c in dispatch.call_args_list
    )


def deliver(user, times=DELIVERIES):
    """Deliver the seed the way repeated activity attempts do."""
    for _ in range(times):
        try:
            seed_pending_demo_data(str(user.organization_id))
        except Exception:
            pass


@pytest.mark.django_db(transaction=True)
def test_new_signup_seed_creates_the_demo_dataset(signup):
    from accounts.models.demo_seed import DemoSeedRequest
    from model_hub.models.choices import SourceChoices
    from model_hub.models.develop_annotations import Annotations, AnnotationsLabels
    from model_hub.models.develop_dataset import Column, Dataset, Row
    from model_hub.models.evals_metric import EvalTemplate
    from tfc.middleware.workspace_context import (
        get_current_organization,
        get_current_workspace,
    )
    from tfc.temporal.background_tasks.activities import seed_demo_data_activity

    user = signup()
    for eval_id in DEMO_EVAL_IDS:
        EvalTemplate.no_workspace_objects.get_or_create(
            eval_id=eval_id,
            defaults={"name": f"eval-{eval_id}", "config": {"config": {}}},
        )
    # A worker runs the seed with no request and no caller bound.
    assert get_current_organization() is None
    assert get_current_workspace() is None

    seed_demo_data_activity(str(user.organization_id))

    intent = DemoSeedRequest.objects.get(organization_id=user.organization_id)
    assert intent.traces_seeded and intent.datasets_seeded
    assert intent.completed_at is not None
    datasets = Dataset.no_workspace_objects.filter(
        organization_id=user.organization_id, deleted=False
    )
    assert set(datasets.values_list("name", flat=True)) == {
        "Demo-dataset",
        "Image-Demo-dataset",
    }
    demo = datasets.get(name="Demo-dataset")
    assert Row.objects.filter(dataset=demo).count() == 50
    # The annotation step logs and carries on when its input is refused, so a
    # finished seed alone does not show the annotation was made.
    annotation = Annotations.no_workspace_objects.get(dataset=demo)
    assert annotation.organization_id == user.organization_id
    assert annotation.workspace_id == demo.workspace_id
    assert list(annotation.assigned_users.values_list("id", flat=True)) == [user.id]
    label = AnnotationsLabels.no_workspace_objects.get(
        organization_id=user.organization_id, name="is_relevant"
    )
    assert list(annotation.labels.all()) == [label]
    assert Column.no_workspace_objects.filter(
        dataset=demo,
        source=SourceChoices.ANNOTATION_LABEL.value,
        source_id=f"{annotation.id}-sourceid-{label.id}",
    ).exists()
    # Nothing stays bound after the seed.
    assert get_current_organization() is None
    assert get_current_workspace() is None


@pytest.mark.django_db(transaction=True)
def test_seed_that_keeps_failing_for_a_lasting_reason_stops(signup):
    user = signup()
    lasting = NotFound("Dataset or column not found")
    with (
        patch("accounts.user_onboard.upload_demo_dataset", side_effect=lasting),
        capture_logs() as logs,
    ):
        # A new signup keeps being retried for a while.
        deliver(user, times=3)
        assert is_dispatched(user)

        age(user)
        with patch(
            "accounts.user_onboard.upload_demo_dataset", side_effect=lasting
        ) as datasets:
            deliver(user)
        # Bounded: once stopped, later deliveries do not run the seed again.
        assert datasets.call_count == 1
        assert not is_dispatched(user)

    stopped = [entry for entry in logs if entry["event"] == "demo_seed_stopped"]
    assert len(stopped) == 1
    assert stopped[0]["organization_id"] == str(user.organization_id)
    assert stopped[0]["reason"] == "NotFound"
    assert "Dataset or column not found" not in repr(stopped[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "error",
    [
        OperationalError("database unavailable"),
        InterfaceError("connection already closed"),
        TimeoutError("timed out"),
        ConnectionRefusedError("queue unavailable"),
    ],
    ids=["database", "connection", "timeout", "queue"],
)
def test_transient_failure_keeps_being_retried(signup, error):
    user = signup()
    age(user)
    with patch(
        "accounts.user_onboard.upload_demo_dataset", side_effect=error
    ) as datasets:
        for _ in range(DELIVERIES):
            with pytest.raises(type(error)):
                seed_pending_demo_data(str(user.organization_id))
    assert datasets.call_count == DELIVERIES
    assert is_dispatched(user)


@pytest.mark.django_db(transaction=True)
def test_stopped_request_runs_again_after_restart(signup):
    from accounts.demo_seeding import restart_demo_seeding
    from accounts.models.demo_seed import DemoSeedRequest

    user = signup()
    age(user)
    with patch(
        "accounts.user_onboard.upload_demo_dataset",
        side_effect=NotFound("Dataset or column not found"),
    ):
        deliver(user)
    assert not is_dispatched(user)

    assert restart_demo_seeding(uuid.uuid4()) == 0
    assert restart_demo_seeding() == 1

    assert is_dispatched(user)
    with patch("accounts.user_onboard.upload_demo_dataset") as datasets:
        seed_pending_demo_data(str(user.organization_id))
    datasets.assert_called_once()
    intent = DemoSeedRequest.objects.get(organization_id=user.organization_id)
    assert intent.traces_seeded and intent.datasets_seeded
    assert intent.completed_at is not None
    # A finished request is not restarted.
    assert restart_demo_seeding() == 0
