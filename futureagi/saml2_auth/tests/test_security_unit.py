from types import SimpleNamespace

from saml2_auth.models import SAMLMetadataModel
from saml2_auth.views import _get_metadata


def test_metadata_loader_uses_the_supplied_organization_record(tmp_path, monkeypatch):
    record = SimpleNamespace(
        relay_state="organization-specific-relay",
        meta="<EntityDescriptor />",
        identity_type=SAMLMetadataModel.IDENTITY_OKTA,
    )
    monkeypatch.setattr("saml2_auth.views.BASE_DIR", str(tmp_path))

    metadata, identity_type = _get_metadata(record)

    metadata_path = tmp_path / "metadata" / "organization-specific-relay.xml"
    assert metadata == {"local": [str(metadata_path)]}
    assert identity_type == SAMLMetadataModel.IDENTITY_OKTA
    assert metadata_path.read_text() == record.meta
