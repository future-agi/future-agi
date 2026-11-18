"""Factory-facing GCS admission facts; synthetic transport, not native proof."""

from dataclasses import replace

import pytest
from google.oauth2.credentials import Credentials

from tracer.models.attachment_storage import (
    BINDING_IDENTITY_FIELDS,
    binding_identity_sha256,
)
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.storage_gcs import GCSPrivateMediaStorage
from tracer.tests.test_attachment_simplify_gcs_transport import (
    CREATED,
    HTTP,
    KMS,
    binding,
    config,
)


@pytest.mark.parametrize("kms", [False, True])
def test_discovery_returns_exact_observed_factory_fields(kms):
    expected = binding()
    transport = HTTP(expected)
    if kms:
        for purpose in ("quarantine", "serving"):
            transport.meta[purpose]["encryption"] = {"defaultKmsKeyName": KMS}
            setattr(expected, f"{purpose}_encryption_mode", "google-kms")
            setattr(expected, f"{purpose}_encryption_key_identity", KMS)
    facts = GCSPrivateMediaStorage.discover_identity(
        config=config(expected),
        posture_credentials=Credentials(token="synthetic-token"),
        http=transport,
    )
    assert set(facts) == set(BINDING_IDENTITY_FIELDS)
    assert facts == {name: getattr(expected, name) for name in BINDING_IDENTITY_FIELDS}
    assert facts["quarantine_created_at"] == CREATED
    assert len(binding_identity_sha256(facts)) == 64
    # The observed tuple must be usable by the same named constructor consumed
    # by the factory; full readiness remains a separate provider posture check.
    adapter = GCSPrivateMediaStorage(
        binding=expected,
        config=config(expected),
        posture_credentials=Credentials(token="synthetic-token"),
        http=transport,
    )
    adapter.verify_readiness()
    assert all(call[0] == "GET" for call in transport.calls)
    assert all(response.closed for response in transport.responses)


@pytest.mark.parametrize(
    "field,value",
    [
        ("projectNumber", "other-project"),
        ("projectNumber", "987654321"),
        ("projectNumber", None),
        ("name", "different-bucket"),
        ("id", "different-resource"),
        ("kind", "storage#object"),
        ("timeCreated", "not-a-timestamp"),
        ("location", "EU"),
        ("encryption", {"defaultKmsKeyName": "unverified-key"}),
        ("encryption", {"defaultKmsKeyName": None}),
        ("hierarchicalNamespace", {"enabled": True}),
    ],
)
def test_discovery_rejects_incomplete_or_inconsistent_metadata(field, value):
    expected = binding()
    transport = HTTP(expected)
    transport.meta["serving"][field] = value
    with pytest.raises(AttachmentError) as error:
        GCSPrivateMediaStorage.discover_identity(
            config=config(expected),
            posture_credentials=Credentials(token="synthetic-token"),
            http=transport,
        )
    assert error.value.code == "storage_unverified"


@pytest.mark.parametrize("posture", [None, object(), Credentials(token=None)])
def test_discovery_requires_explicit_valid_posture_without_refresh(posture):
    expected = binding()
    transport = HTTP(expected)
    with pytest.raises(AttachmentError):
        GCSPrivateMediaStorage.discover_identity(
            config=config(expected), posture_credentials=posture, http=transport
        )
    assert transport.calls == []


def test_discovery_rejects_redirected_default_endpoint_before_request():
    expected = binding()
    settings = config(expected)
    transport = HTTP(expected)
    with pytest.raises(AttachmentError):
        GCSPrivateMediaStorage.discover_identity(
            config=replace(
                settings,
                storage=replace(settings.storage, endpoint="different.invalid"),
            ),
            posture_credentials=Credentials(token="synthetic-token"),
            http=transport,
        )
    assert transport.calls == []
