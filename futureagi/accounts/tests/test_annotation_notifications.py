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
