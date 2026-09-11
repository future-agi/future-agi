import pytest
from rest_framework import status as http_status

from accounts.models.workspace import Workspace
from model_hub.models.tts_voices import TTSVoice


def _result(response):
    payload = response.json()
    return payload.get("result", payload)


def _rows(response):
    payload = response.json()
    if isinstance(payload, list):
        return payload
    if isinstance(payload.get("results"), list):
        return payload["results"]
    if isinstance(payload.get("result"), list):
        return payload["result"]
    return []


@pytest.mark.django_db
def test_tts_voice_lifecycle_scopes_workspace_and_soft_deletes(
    auth_client, organization, workspace, user
):
    hidden_voice = TTSVoice.no_workspace_objects.create(
        name="Workspace local TTS voice",
        description="same name in another workspace should be allowed",
        voice_id="hidden-rime-voice",
        provider="rime",
        model="arcana",
        organization=organization,
        workspace=workspace,
    )
    other_workspace = Workspace.objects.create(
        name="TTS Voice Other Workspace",
        organization=organization,
        created_by=user,
    )

    auth_client.set_workspace(other_workspace)
    create_response = auth_client.post(
        "/model-hub/tts-voices/",
        {
            "name": "Workspace local TTS voice",
            "description": "active workspace TTS voice",
            "voice_id": "active-rime-voice",
            "provider": "rime",
            "model": "arcana",
        },
        format="json",
    )

    assert create_response.status_code == http_status.HTTP_200_OK
    created = _result(create_response)
    voice_id = created["id"]
    voice = TTSVoice.no_workspace_objects.get(id=voice_id)
    assert voice.organization == organization
    assert voice.workspace == other_workspace
    assert voice.voice_type == "custom"

    list_response = auth_client.get("/model-hub/tts-voices/")
    assert list_response.status_code == http_status.HTTP_200_OK
    list_ids = {row["id"] for row in _rows(list_response)}
    assert voice_id in list_ids
    assert str(hidden_voice.id) not in list_ids

    detail_response = auth_client.get(f"/model-hub/tts-voices/{voice_id}/")
    assert detail_response.status_code == http_status.HTTP_200_OK
    assert detail_response.json()["name"] == "Workspace local TTS voice"

    hidden_detail_response = auth_client.get(
        f"/model-hub/tts-voices/{hidden_voice.id}/"
    )
    assert hidden_detail_response.status_code == http_status.HTTP_404_NOT_FOUND

    duplicate_response = auth_client.post(
        "/model-hub/tts-voices/",
        {
            "name": "Workspace local TTS voice",
            "description": "duplicate active workspace TTS voice",
            "voice_id": "duplicate-rime-voice",
            "provider": "rime",
            "model": "arcana",
        },
        format="json",
    )
    assert duplicate_response.status_code == http_status.HTTP_400_BAD_REQUEST

    replace_response = auth_client.put(
        f"/model-hub/tts-voices/{voice_id}/",
        {
            "name": "Workspace local TTS voice final",
            "description": "final TTS voice",
            "voice_id": "active-rime-voice-final",
            "provider": "rime",
            "model": "mist",
        },
        format="json",
    )
    assert replace_response.status_code == http_status.HTTP_200_OK
    voice.refresh_from_db()
    assert voice.name == "Workspace local TTS voice final"
    assert voice.voice_id == "active-rime-voice-final"
    assert voice.model == "mist"
    assert voice.workspace == other_workspace

    patch_response = auth_client.patch(
        f"/model-hub/tts-voices/{voice_id}/",
        {"description": "patched TTS voice"},
        format="json",
    )
    assert patch_response.status_code == http_status.HTTP_200_OK
    voice.refresh_from_db()
    assert voice.description == "patched TTS voice"
    assert voice.workspace == other_workspace

    delete_response = auth_client.delete(f"/model-hub/tts-voices/{voice_id}/")
    assert delete_response.status_code == http_status.HTTP_204_NO_CONTENT
    voice.refresh_from_db()
    assert voice.deleted is True
    assert voice.deleted_at is not None

    deleted_detail_response = auth_client.get(f"/model-hub/tts-voices/{voice_id}/")
    assert deleted_detail_response.status_code == http_status.HTTP_404_NOT_FOUND

    hidden_voice.refresh_from_db()
    assert hidden_voice.deleted is False
    assert hidden_voice.workspace == workspace
