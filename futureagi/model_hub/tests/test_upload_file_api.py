import base64
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status as http_status


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def _result(response):
    payload = response.json()
    return payload.get("result", payload)


@pytest.mark.django_db
def test_upload_file_multipart_preserves_file_name_and_storage_extension(
    auth_client, organization, monkeypatch
):
    calls = []

    def fake_upload_image(item, bucket_name=None, object_key=None, org_id=None):
        calls.append(
            {
                "item": item,
                "bucket_name": bucket_name,
                "object_key": object_key,
                "org_id": org_id,
            }
        )
        return "http://localhost:9005/fi-content-dev/tempcust/generated.png"

    monkeypatch.setattr(
        "model_hub.views.prompt_template.upload_image_to_s3", fake_upload_image
    )

    response = auth_client.post(
        "/model-hub/upload-file/",
        {
            "type": "image",
            "files": [
                SimpleUploadedFile(
                    "prompt-image.png", PNG_BYTES, content_type="image/png"
                )
            ],
        },
        format="multipart",
    )

    assert response.status_code == http_status.HTTP_200_OK
    assert _result(response) == [
        {
            "url": "http://localhost:9005/fi-content-dev/tempcust/generated.png",
            "file_name": "prompt-image.png",
        }
    ]
    assert len(calls) == 1
    assert calls[0]["item"].startswith("data:image/png;base64,")
    assert calls[0]["bucket_name"] == "fi-customer-data-dev"
    assert calls[0]["object_key"] is None
    assert calls[0]["org_id"] == str(organization.id)


@pytest.mark.django_db
def test_upload_file_link_infers_file_extension_without_forcing_object_key(
    auth_client, organization, monkeypatch
):
    calls = []

    def fake_upload_document(item, bucket_name=None, object_key=None, org_id=None):
        calls.append(
            {
                "item": item,
                "bucket_name": bucket_name,
                "object_key": object_key,
                "org_id": org_id,
            }
        )
        return "http://localhost:9005/fi-content-dev/tempcust/generated.pdf"

    monkeypatch.setattr(
        "model_hub.views.prompt_template.upload_document_to_s3",
        fake_upload_document,
    )
    monkeypatch.setattr(
        "model_hub.views.prompt_template.requests.head",
        lambda url, timeout: SimpleNamespace(
            headers={"Content-Type": "application/pdf; charset=utf-8"}
        ),
    )

    response = auth_client.post(
        "/model-hub/upload-file/",
        {
            "type": "pdf",
            "links": ["https://example.test/reports/eval-output"],
        },
        format="json",
    )

    assert response.status_code == http_status.HTTP_200_OK
    assert _result(response) == [
        {
            "url": "http://localhost:9005/fi-content-dev/tempcust/generated.pdf",
            "file_name": "eval-output.pdf",
        }
    ]
    assert calls == [
        {
            "item": "https://example.test/reports/eval-output",
            "bucket_name": "fi-customer-data-dev",
            "object_key": None,
            "org_id": str(organization.id),
        }
    ]


@pytest.mark.django_db
def test_upload_file_rejects_missing_source_before_storage(auth_client, monkeypatch):
    upload_calls = []
    monkeypatch.setattr(
        "model_hub.views.prompt_template.upload_image_to_s3",
        lambda *args, **kwargs: upload_calls.append((args, kwargs)),
    )

    response = auth_client.post(
        "/model-hub/upload-file/",
        {"type": "image"},
        format="multipart",
    )

    assert response.status_code == http_status.HTTP_400_BAD_REQUEST
    assert upload_calls == []
