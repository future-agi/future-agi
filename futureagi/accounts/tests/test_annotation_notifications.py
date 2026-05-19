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
    assert response.data["status"] is False
    assert response.data["message"] == "timeZone: Unknown field."
    assert response.data["details"] == {"timeZone": ["Unknown field."]}

