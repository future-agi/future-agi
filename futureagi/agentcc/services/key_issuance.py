"""Persist personal key identity before provisioning its hash on the gateway."""

import hashlib
import secrets
import uuid

import structlog
from django.db import transaction
from django.utils import timezone

from agentcc.models import AgentccAPIKey
from agentcc.services import auth_bridge
from agentcc.services.gateway_client import GatewayClientError
from agentcc.services.gateway_membership import live_gateway_memberships
from agentcc.services.key_audit import KeyAuditUnavailable

logger = structlog.get_logger(__name__)


class KeyTransactionUnavailable(KeyAuditUnavailable):
    """The audited change cannot own its required local commit boundary."""


class Issuance:
    def __init__(self, using):
        self.using = using
        self.api_key = None
        self.committed = False
        self.provisioned = False

    def provision_key(
        self, *, name, owner, user, organization, project=None,
        models=None, providers=None, metadata=None,
    ):
        connection = transaction.get_connection(self.using)
        if self.api_key is not None or not connection.in_atomic_block:
            raise KeyTransactionUnavailable("Key transaction unavailable")
        if (
            not user or not organization or not user.is_active
            or not live_gateway_memberships().using(self.using).filter(
                user=user, organization=organization
            ).exists()
        ):
            raise KeyTransactionUnavailable("Key transaction unavailable")
        # Same entropy/format as the gateway generator, but the identity and
        # hash must commit with the required audit BEFORE any remote issuance.
        raw_key = auth_bridge.GATEWAY_KEY_SCHEME + secrets.token_hex(24)
        self.api_key = AgentccAPIKey.unscoped_objects.using(self.using).create(
            gateway_key_id="key_" + uuid.uuid4().hex,
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
            key_prefix=auth_bridge.display_key_prefix(raw_key),
            name=name, owner=user.email, user=user, organization=organization,
            workspace=None, project=project, status=AgentccAPIKey.PENDING,
            allowed_models=models or [], allowed_providers=providers or [],
            metadata=auth_bridge.bound_key_metadata(metadata, organization.pk),
        )
        return self.api_key, raw_key

    def after_commit(self):
        """Own callback: failures cannot skip the old-key revocation callback.

        The raw secret is never sent to the gateway or stored here. A failed
        import keeps a committed non-active row; failure to record FAILED still
        leaves PENDING. Neither state is loadable by the ordinary sync path.
        Unknown transport outcomes may leave an unreturned remote hash. Its
        identity and actor audit are already committed, never compensated away.
        """
        self.committed = True
        if self.api_key is None:
            return
        key = self.api_key
        gateway = None
        try:
            key.refresh_from_db(using=self.using)
            if key.deleted or key.status != AgentccAPIKey.PENDING:
                return
            payload = auth_bridge.gateway_key_payload(key, server_metadata={})
            gateway = auth_bridge.get_gateway_client()
            if gateway.import_committed_key(payload) is not True:
                raise KeyTransactionUnavailable("Key provisioning unconfirmed")
            # Never revive a concurrently revoked/deleted pending key.
            changed = AgentccAPIKey.unscoped_objects.using(self.using).filter(
                pk=key.pk, deleted=False, status=AgentccAPIKey.PENDING,
            ).update(status=AgentccAPIKey.ACTIVE, updated_at=timezone.now())
            if changed != 1:
                raise KeyTransactionUnavailable("Key provisioning unconfirmed")
            key.status = AgentccAPIKey.ACTIVE
            self.provisioned = True
        except Exception:
            # Do not delete the committed identity or audit, even on timeout.
            # Recording FAILED is best effort; PENDING is already fail-closed.
            try:
                AgentccAPIKey.unscoped_objects.using(self.using).filter(
                    pk=key.pk, status=AgentccAPIKey.PENDING,
                ).update(status=AgentccAPIKey.FAILED, updated_at=timezone.now())
            except Exception:
                logger.warning("gateway_key_provisioning_state_pending")
            logger.warning("gateway_key_provisioning_unconfirmed")
            # No import was attempted if client acquisition failed. Do not
            # acquire a second client just to revoke; bulk still retires FAILED.
            if gateway is None:
                return
            # FAILED remains the write-ahead negative fact even if this push
            # is unconfirmed. The ID is only a retirement handle, not authority.
            try:
                confirmed = gateway.revoke_key_confirmed(key.gateway_key_id)
                outcome = "confirmed" if confirmed is True else "unconfirmed"
            except GatewayClientError as error:
                # Nothing to revoke on a gateway which never held this ID.
                outcome = "not_found" if error.status_code == 404 else "failed"
            except Exception:
                outcome = "failed"
            # Never include the exception, response, ID, prefix or key hash.
            logger.warning("gateway_key_provisioning_revoke", outcome=outcome)
