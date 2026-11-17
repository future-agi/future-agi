"""
A voice call's provider credentials, read by call id inside the activity that
dials.

Activity inputs and results carry the call id; the activity that dials reads
the customer's provider credentials from the call's agent version when it
needs them. A value an input already carries is used as it is.
"""

from dataclasses import dataclass, field

from asgiref.sync import sync_to_async

# Metadata keys that hold a customer credential value.
SECRET_METADATA_KEYS = frozenset(
    {
        "customer_api_key",
        "customer_livekit_api_key",
        "customer_livekit_api_secret",
    }
)


@dataclass(frozen=True)
class CustomerCredentials:
    """The customer's provider credentials for one call. Values never print."""

    api_key: str = field(default="", repr=False)
    livekit_url: str = ""
    livekit_api_key: str = field(default="", repr=False)
    livekit_api_secret: str = field(default="", repr=False)


def strip_customer_secrets(metadata: dict | None) -> dict:
    """Return a copy of ``metadata`` without the credential keys."""
    return {
        key: value
        for key, value in (metadata or {}).items()
        if key not in SECRET_METADATA_KEYS
    }


async def read_customer_credentials(call_id: str) -> CustomerCredentials:
    """Read the provider credentials of the call's agent version.

    The read follows the call to its agent version and that version's
    credentials row, so it returns only the call's own credentials. A call
    without an agent version has none. Raises CallExecution.DoesNotExist for an
    unknown call id.
    """
    from simulate.models import AgentVersion
    from simulate.models.test_execution import CallExecution
    from simulate.services.agent_definition import resolve_api_key_for_version

    call = await CallExecution.objects.select_related(
        "agent_version",
        "agent_version__agent_definition",
        "agent_version__credentials",
    ).aget(id=call_id)
    version = call.agent_version
    if version is None:
        return CustomerCredentials()

    api_key = await sync_to_async(resolve_api_key_for_version)(version) or ""

    try:
        row = version.credentials
    except AgentVersion.credentials.RelatedObjectDoesNotExist:
        row = None
    if row is None:
        return CustomerCredentials(api_key=api_key)

    return CustomerCredentials(
        api_key=api_key,
        livekit_url=row.server_url or "",
        livekit_api_key=row.get_api_key() or "",
        livekit_api_secret=row.get_api_secret() or "",
    )
