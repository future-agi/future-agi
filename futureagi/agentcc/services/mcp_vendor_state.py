"""Short binding locks and optimistic snapshots; never call a vendor here."""

from agentcc.models.mcp_vendor import AgentccMCPVendorBinding as Binding


def locked_bindings():
    # select_related must not lock the shared org or vault row implicitly.
    return Binding.objects.select_for_update(of=("self",)).select_related(
        "organization", "credential"
    )


def revision(binding):
    return (
        binding.version,
        binding.credential_id,
        binding.credential.version,
        binding.credential.status,
        binding.credential.token_expires_at,
    )


def current(binding, snapshot):
    return binding.enabled and revision(binding) == revision(snapshot)
