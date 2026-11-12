"""Local revoke first; vendor I/O outside transactions; optimistic apply."""

from django.db import transaction

from agentcc.models.mcp_vendor import AgentccMCPVendorBinding as Binding
from agentcc.models.mcp_vendor import AgentccMCPVendorConnection as Connection
from agentcc.models.mcp_vendor import AgentccMCPVendorReceipt as Receipt
from agentcc.services import mcp_vendor_cleanup as cleanup
from agentcc.services import mcp_vendor_lease as lease
from agentcc.services.credential_manager import decrypt_token, encrypt_token
from agentcc.services.gateway_admin import mcp_credentials as gateway
from agentcc.services.mcp_revocation import Revocation
from agentcc.services.mcp_vendor_credentials import (
    authorize,
    enabled,
    subject_key,
    vendor_client,
    vendor_signs_in,
)
from agentcc.services.mcp_vendor_state import current, locked_bindings


class ConnectionRefused(Exception):
    def __init__(self, code, status=409):
        self.code = code
        self.status = status
        super().__init__(code)


def _receipt(row, step, result):
    Receipt.objects.create(
        connection=row,
        organization_id=row.binding.organization_id,
        connection_ref=row.pk,
        server_id=row.binding.server_id,
        consent_generation=row.consent_generation,
        step=step,
        outcome=result.outcome,
        reason=result.reason,
    )


def require_microsoft_binding_enabled(
    org_id, server_id, *, app_slug="", toolkits=(), binding=None
):
    """Preflight both binding creation and reservation without vendor custody.

    An existing ``binding`` whose vendor signs people in with its own app
    (``vendor_signs_in``) is not held. Creating a binding (none yet) still is.
    """
    from agentcc.services import microsoft_connect_gate as microsoft
    from agentcc.services.mcp_upstream_oauth import server_entry

    if binding is not None and vendor_signs_in(binding):
        return
    entry = server_entry(org_id, server_id, include_hidden=True)
    microsoft.require_microsoft_connect_enabled(
        entry, server_id=server_id, app_id=app_slug
    )
    for toolkit in toolkits:
        microsoft.require_microsoft_connect_enabled(app_id=toolkit)


def _reserve(binding_id, identity):
    # Match W4 supersede: retry vendor once; only the gateway purge is a hard
    # reconnect barrier. The new generation cannot adopt the old account.
    bound = Binding.objects.get(pk=binding_id)
    if not authorize(bound, identity)[1]:
        raise ConnectionRefused("identity_refused", 403)
    require_microsoft_binding_enabled(
        bound.organization_id, bound.server_id,
        app_slug=bound.app_slug, toolkits=bound.toolkits, binding=bound,
    )
    subject = subject_key(bound.organization_id, identity["source"], identity["id"])
    old = Connection.objects.filter(binding_id=binding_id, end_user_key=subject).first()
    recovered = False
    if old and lease.expired(old):
        with transaction.atomic():
            locked_bindings().get(pk=binding_id)
            old = Connection.objects.select_for_update().get(pk=old.pk)
            recovered = lease.recover(old)
        if recovered:
            old = disconnect(old.pk)
    if old and (old.gateway_purge_pending or old.connect_pending):
        raise ConnectionRefused("cleanup_pending")
    if (
        old
        and not recovered
        and (
            old.vendor_revoke_pending
            or (
                old.status != "revoked"
                and (old.vendor_account_ref or old.status == "active")
            )
            or (
                bound.vendor == "pipedream"
                and old.status == "pending"
                and old.version > 1
            )
        )
    ):
        # Reconnect is a revoke/purge/supersede, including pending consent.
        # Never overwrite the last handle to an account whose grant may survive.
        old = disconnect(old.pk)
    with transaction.atomic():
        binding = locked_bindings().get(pk=binding_id)
        user, allowed = authorize(binding, identity)
        if not allowed:
            raise ConnectionRefused("identity_refused", 403)
        if not binding.enabled or binding.credential.status != "active":
            raise ConnectionRefused("no_credential")
        if binding.vendor == "composio" and len(binding.toolkits) != 1:
            raise ConnectionRefused("single_toolkit_required", 400)
        row, created = Connection.objects.get_or_create(
            binding=binding,
            end_user_key=subject,
            defaults={"source": identity["source"], "user": user},
        )
        if (
            row.gateway_purge_pending
            or row.connect_pending
            or (old is None and not created)
            or (old is not None and row.version != old.version)
            or (
                row.status != "revoked"
                and (row.vendor_account_ref or row.status == "active")
            )
        ):
            raise ConnectionRefused("cleanup_pending")
        if row.vendor_revoke_pending:
            _receipt(row, "vendor_revocation", Revocation("unconfirmed", "superseded"))
            row.vendor_revoke_pending = False
            row.vendor_revoke_outcome = "unconfirmed"
        if row.status == "revoked":
            cleanup.remember(row)
            row.consent_generation += 1
            row.vendor_account_ref = ""
            row.encrypted_session = row.encrypted_endpoint = None
            row.offboard_requested = False
        row.version += 1
        row.status = "pending"
        lease.begin(row)
        return binding, row


def _save_session(binding, row, session, endpoint):
    # This commits BEFORE creating a link, including on the asserted endpoint.
    # A concurrent revoke still gets the minted handle, and owes another cleanup.
    with transaction.atomic():
        locked_bindings().get(pk=binding.pk)
        saved = Connection.objects.select_for_update().get(pk=row.pk)
        lease.recover(saved)
        row.encrypted_session = encrypt_token(session)
        row.encrypted_endpoint = encrypt_token(endpoint)
        cleanup.remember(row)
        if not lease.owns(saved, row):
            return False
        saved.encrypted_session = row.encrypted_session
        saved.encrypted_endpoint = row.encrypted_endpoint
        if saved.version != row.version:
            saved.vendor_revoke_pending = True
            saved.vendor_revoke_outcome = "pending"
        saved.save()
        return saved.version == row.version


def start(binding_id, identity):
    if not enabled():
        raise ConnectionRefused("not_found", 404)
    binding, row = _reserve(binding_id, identity)
    try:
        api = vendor_client(binding)
        if binding.vendor == "pipedream":
            link = api.connect(row.vendor_subject)
            account = ""
        else:
            if not row.encrypted_session:
                session, endpoint = api.create(
                    row.vendor_subject, binding.toolkits, binding.tools
                )
                if not _save_session(binding, row, session, endpoint):
                    raise ConnectionRefused("revoked")
            link = api.connect(
                decrypt_token(row.encrypted_session), binding.toolkits[0]
            )
            account = api.connected_account_ref
        with transaction.atomic():
            fresh = locked_bindings().get(pk=binding.pk)
            saved = Connection.objects.select_for_update().get(pk=row.pk)
            lease.recover(saved)
            stale = (
                not current(fresh, binding)
                or saved.version != row.version
                or not lease.owns(saved, row)
                or not authorize(fresh, identity)[1]
            )
            # The connect_pending barrier prevents another connect generation.
            # A concurrent revoke still needs this newly returned account handle.
            if account:
                row.vendor_account_ref = account
                cleanup.remember(row)
                if lease.owns(saved, row):
                    saved.vendor_account_ref = account
                    saved.save(update_fields=["vendor_account_ref", "updated_at"])
        if stale:
            raise ConnectionRefused("revoked")
        return link
    finally:
        # Do not roll back session references if link creation or authorization fails.
        with transaction.atomic():
            locked_bindings().get(pk=binding.pk)
            saved = Connection.objects.select_for_update().get(pk=row.pk)
            lease.finish(row, saved)


def purge_gateway(row):
    # Stage 2 adds vendor-aware, per-end-user cache invalidation.
    return gateway.purge(str(row.binding.organization_id), row.binding.server_id)


def revoke_vendor(row, *, offboard=False):
    return cleanup.run(row, _revoke_one, offboard=offboard)


def _revoke_one(row, *, offboard=False):
    if row.binding.credential.status != "active":
        return Revocation("unconfirmed", "credential_inactive")
    api = vendor_client(row.binding)
    if row.binding.vendor == "pipedream" and offboard:
        return api.offboard(row.vendor_subject)
    account = row.vendor_account_ref
    if not account:
        if row.binding.vendor == "pipedream":
            account = api.account(row.vendor_subject, row.binding.app_slug)
        elif row.encrypted_session and len(row.binding.toolkits) == 1:
            account = api.account(
                decrypt_token(row.encrypted_session), row.binding.toolkits[0]
            )
        if account:
            # Retain a discovered handle even if revoke/delete needs a retry.
            Connection.objects.filter(
                pk=row.pk,
                version=row.version,
                consent_generation=row.consent_generation,
            ).update(vendor_account_ref=account)
            row.vendor_account_ref = account
    result = (
        api.disconnect(account) if account else Revocation("unconfirmed", "no_account")
    )
    if row.binding.vendor == "pipedream":
        return result
    session = (
        api.offboard(decrypt_token(row.encrypted_session))
        if row.encrypted_session
        else Revocation("confirmed")
    )
    for outcome in ("pending", "unconfirmed"):
        for item in (result, session):
            if item.outcome == outcome:
                return item
    return Revocation("confirmed")


def disconnect(connection_id, *, offboard=False):
    binding_id = Connection.objects.values_list("binding_id", flat=True).get(
        pk=connection_id
    )
    with transaction.atomic():
        binding = locked_bindings().get(pk=binding_id)
        row = Connection.objects.select_for_update().get(pk=connection_id)
        row.binding = binding
        lease.recover(row)
        if row.status != "revoked" or (offboard and not row.offboard_requested):
            row.offboard_requested = row.offboard_requested or offboard
            row.status = "revoked"
            row.version += 1
            row.vendor_revoke_pending = True
            row.vendor_revoke_outcome = "pending"
            row.gateway_purge_pending = True
            row.save()
            _receipt(row, "local_revoke", Revocation("confirmed"))
        cleanup.remember(row)
    purged, result = None, None
    if row.gateway_purge_pending:
        try:
            purged = bool(purge_gateway(row))
        except Exception:
            purged = False
    if row.vendor_revoke_pending and not row.connect_pending:
        try:
            result = (
                revoke_vendor(row, offboard=True)
                if row.offboard_requested
                else revoke_vendor(row)
            )
        except Exception:
            result = Revocation("pending", "vendor_unavailable")
    with transaction.atomic():
        locked_bindings().get(pk=binding_id)
        saved = Connection.objects.select_for_update().get(pk=connection_id)
        if saved.version != row.version or saved.status != "revoked":
            return saved
        if purged is not None and saved.gateway_purge_pending:
            saved.gateway_purge_pending = not purged
            _receipt(
                saved,
                "gateway_purge",
                Revocation(
                    "confirmed" if purged else "pending",
                    "" if purged else "purge_failed",
                ),
            )
        if (
            result is not None
            and saved.vendor_revoke_pending
            and not saved.connect_pending
        ):
            saved.vendor_revoke_outcome = result.outcome
            saved.vendor_revoke_pending = result.outcome == "pending"
            if binding.vendor == "pipedream" and result.reason == "not_documented":
                _receipt(saved, "partner_record_delete", Revocation("confirmed"))
            _receipt(saved, "vendor_revocation", result)
            if not saved.vendor_revoke_pending:
                saved.encrypted_session = saved.encrypted_endpoint = None
                saved.vendor_account_ref = ""
        saved.save()
    return saved


def offboard_member(org_id, user_id):
    """Called after membership changes commit; bulk callers must invoke explicitly."""
    if not enabled():
        return
    ids = Connection.objects.filter(
        binding__organization_id=org_id, user_id=user_id
    ).values_list("id", flat=True)
    for connection_id in list(ids):
        disconnect(connection_id, offboard=True)


def disable_binding(binding_id, *, delete=False):
    with transaction.atomic():
        binding = locked_bindings().get(pk=binding_id)
        binding.enabled = False
        binding.version += 1
        binding.save(update_fields=["enabled", "version"])
    for pk in list(
        Connection.objects.filter(binding_id=binding_id).values_list("pk", flat=True)
    ):
        disconnect(pk, offboard=True)
    with transaction.atomic():
        binding = locked_bindings().get(pk=binding_id)
        if delete:
            if (
                binding.connections.filter(gateway_purge_pending=True).exists()
                or binding.connections.filter(vendor_revoke_pending=True).exists()
                or binding.connections.filter(connect_pending=True).exists()
            ):
                raise ConnectionRefused("cleanup_pending")
            binding.delete()
    return binding
