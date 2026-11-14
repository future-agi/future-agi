import pytest
from rest_framework import status as http_status

from accounts.models.workspace import Workspace
from model_hub.models.api_key import SecretModel, SecretType


def _rows(response):
    payload = response.json()
    if isinstance(payload, list):
        return payload
    if isinstance(payload.get("results"), list):
        return payload["results"]
    if isinstance(payload.get("result"), list):
        return payload["result"]
    return []


def _assert_secret_response_is_masked(payload, *raw_values):
    text = str(payload)
    assert "key" not in payload
    for raw_value in raw_values:
        assert raw_value not in text
    assert payload.get("masked_key")


@pytest.mark.django_db
def test_secret_lifecycle_scopes_workspace_masks_and_preserves_encryption(
    auth_client, organization, workspace, user
):
    raw_other_secret = "other-workspace-secret-value"
    hidden_secret = SecretModel.no_workspace_objects.create(
        name="Workspace local secret",
        description="same name in another workspace should be allowed",
        secret_type=SecretType.API_KEY,
        key=raw_other_secret,
        organization=organization,
        workspace=workspace,
    )
    other_workspace = Workspace.objects.create(
        name="Secret Other Workspace",
        organization=organization,
        created_by=user,
    )

    auth_client.set_workspace(other_workspace)
    raw_secret = "active-workspace-secret-value"
    create_response = auth_client.post(
        "/model-hub/secrets/",
        {
            "name": "Workspace local secret",
            "description": "active workspace secret",
            "secret_type": SecretType.API_KEY,
            "key": raw_secret,
        },
        format="json",
    )

    assert create_response.status_code == http_status.HTTP_201_CREATED
    created = create_response.json()
    secret_id = created["id"]
    _assert_secret_response_is_masked(created, raw_secret)

    secret = SecretModel.no_workspace_objects.get(id=secret_id)
    assert secret.organization == organization
    assert secret.workspace == other_workspace
    assert secret.key != raw_secret
    assert secret.actual_key == raw_secret
    encrypted_key = secret.key

    list_response = auth_client.get("/model-hub/secrets/")
    assert list_response.status_code == http_status.HTTP_200_OK
    list_rows = _rows(list_response)
    list_ids = {row["id"] for row in list_rows}
    assert secret_id in list_ids
    assert str(hidden_secret.id) not in list_ids
    listed = next(row for row in list_rows if row["id"] == secret_id)
    _assert_secret_response_is_masked(listed, raw_secret, raw_other_secret)

    detail_response = auth_client.get(f"/model-hub/secrets/{secret_id}/")
    assert detail_response.status_code == http_status.HTTP_200_OK
    _assert_secret_response_is_masked(detail_response.json(), raw_secret)

    hidden_detail_response = auth_client.get(f"/model-hub/secrets/{hidden_secret.id}/")
    assert hidden_detail_response.status_code == http_status.HTTP_404_NOT_FOUND

    patch_response = auth_client.patch(
        f"/model-hub/secrets/{secret_id}/",
        {"description": "metadata-only patch"},
        format="json",
    )
    assert patch_response.status_code == http_status.HTTP_200_OK
    _assert_secret_response_is_masked(patch_response.json(), raw_secret)
    secret.refresh_from_db()
    assert secret.description == "metadata-only patch"
    assert secret.workspace == other_workspace
    assert secret.key == encrypted_key
    assert secret.actual_key == raw_secret

    rotated_secret = "rotated-active-workspace-secret-value"
    replace_response = auth_client.put(
        f"/model-hub/secrets/{secret_id}/",
        {
            "name": "Workspace local secret final",
            "description": "final secret",
            "secret_type": SecretType.TOKEN,
            "key": rotated_secret,
        },
        format="json",
    )
    assert replace_response.status_code == http_status.HTTP_200_OK
    _assert_secret_response_is_masked(
        replace_response.json(), raw_secret, rotated_secret
    )
    secret.refresh_from_db()
    assert secret.name == "Workspace local secret final"
    assert secret.secret_type == SecretType.TOKEN
    assert secret.workspace == other_workspace
    assert secret.key != encrypted_key
    assert secret.key != rotated_secret
    assert secret.actual_key == rotated_secret
    rotated_encrypted_key = secret.key

    delete_response = auth_client.delete(f"/model-hub/secrets/{secret_id}/")
    assert delete_response.status_code == http_status.HTTP_204_NO_CONTENT
    secret.refresh_from_db()
    assert secret.deleted is True
    assert secret.deleted_at is not None
    assert secret.key == rotated_encrypted_key
    assert secret.actual_key == rotated_secret

    deleted_detail_response = auth_client.get(f"/model-hub/secrets/{secret_id}/")
    assert deleted_detail_response.status_code == http_status.HTTP_404_NOT_FOUND

    hidden_secret.refresh_from_db()
    assert hidden_secret.deleted is False
    assert hidden_secret.workspace == workspace
    assert hidden_secret.actual_key == raw_other_secret
