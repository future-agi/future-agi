from types import SimpleNamespace

from saml2_auth.models import SAMLMetadataModel
from saml2_auth.views import _get_metadata


def test_metadata_loader_uses_current_metadata_from_the_organization_record():
    record = SimpleNamespace(
        relay_state="organization-specific-relay",
        meta="<EntityDescriptor />",
        identity_type=SAMLMetadataModel.IDENTITY_OKTA,
    )
    metadata, identity_type = _get_metadata(record)

    assert metadata == {"inline": [record.meta]}
    assert identity_type == SAMLMetadataModel.IDENTITY_OKTA

    record.meta = "<EntityDescriptor entityID='rotated-idp' />"
    metadata, _ = _get_metadata(record)

    assert metadata == {"inline": [record.meta]}
