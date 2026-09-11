import pytest


@pytest.mark.django_db
def test_user_timezone_updates_authenticated_user(auth_client, user):
    response = auth_client.post(
        "/accounts/me/timezone/",
        {"timezone": "America/Los_Angeles"},
        format="json",
    )

    assert response.status_code == 200
    assert response.data == {"timezone": "America/Los_Angeles"}
    user.refresh_from_db()
    assert user.last_timezone == "America/Los_Angeles"


@pytest.mark.django_db
def test_user_timezone_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        "/accounts/me/timezone/",
        {"timezone": "UTC", "timeZone": "UTC"},
        format="json",
    )

    assert response.status_code == 400
    data = response.data
    assert data["status"] is False
    assert data["message"] == "timeZone: Unknown field."
    assert data["details"] == {"timeZone": ["Unknown field."]}


@pytest.mark.django_db
def test_user_timezone_invalid_value_uses_error_envelope(auth_client):
    response = auth_client.post(
        "/accounts/me/timezone/",
        {"timezone": "not-a-timezone"},
        format="json",
    )

    assert response.status_code == 400
    data = response.data
    assert data["status"] is False
    assert data["type"] == "validation_error"
    assert data["code"] == "invalid"
    assert data["detail"] == "Invalid timezone."
    assert data["message"] == data["detail"]
    assert data["error"] == data["detail"]
    assert data["result"] == data["detail"]


@pytest.mark.django_db
def test_unsubscribe_annotation_digest_happy_path(api_client, user):
    """Valid token disables both digest tracks."""
    from model_hub.models.annotation_queues import AnnotationNotificationState
    from model_hub.utils.annotation_digest import _unsubscribe_token

    state, _ = AnnotationNotificationState.objects.get_or_create(user=user)
    assert state.digest_enabled is True

    token = _unsubscribe_token(user.id)
    response = api_client.get(f"/accounts/notifications/unsubscribe/?token={token}")
    assert response.status_code == 200
    assert b"Unsubscribed" in response.content
    assert user.email.encode() in response.content

    state.refresh_from_db()
    assert state.digest_enabled is False


@pytest.mark.django_db
def test_unsubscribe_annotation_digest_invalid_token(api_client):
    """Invalid token returns expired/invalid HTML page."""
    response = api_client.get("/accounts/notifications/unsubscribe/?token=not-valid")
    assert response.status_code == 200
    assert b"Link expired" in response.content


@pytest.mark.django_db
def test_snooze_annotation_digest_happy_path(api_client, user):
    """Valid token snoozes realtime track for N days."""
    from django.utils import timezone

    from model_hub.models.annotation_queues import AnnotationNotificationState
    from model_hub.utils.annotation_digest import _unsubscribe_token

    token = _unsubscribe_token(user.id)
    before = timezone.now()
    response = api_client.get(f"/accounts/notifications/snooze/?token={token}&days=3")
    assert response.status_code == 200
    assert b"Snoozed" in response.content

    state = AnnotationNotificationState.objects.get(user=user)
    assert state.realtime_snoozed_until is not None
    # ~3 days from now (allow small clock skew)
    delta = state.realtime_snoozed_until - before
    assert 2.9 * 24 * 3600 <= delta.total_seconds() <= 3.1 * 24 * 3600


@pytest.mark.django_db
def test_snooze_annotation_digest_invalid_token(api_client):
    """Invalid snooze token returns expired HTML page."""
    response = api_client.get("/accounts/notifications/snooze/?token=bad")
    assert response.status_code == 200
    assert b"Link expired" in response.content
