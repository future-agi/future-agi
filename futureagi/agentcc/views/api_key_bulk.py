import re
from datetime import timedelta

import structlog
from django.db import connection
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView

from agentcc.models import AgentccAPIKey
from agentcc.permissions import IsAdminToken
from agentcc.serializers.contracts import (
    AgentccErrorResponseSerializer,
    APIKeyBulkResponseSerializer,
)
from agentcc.services.auth_bridge import (
    gateway_inflight_keys, gateway_key_payloads, gateway_loadable_keys,
)
from agentcc.services.delegation_snapshot import delegation_snapshot
from tfc.routers import primary_required
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

# The error code of the 503 answer while the database has no key table yet.
# agentcc-gateway internal/auth/sync.go (MigrationsPendingCode) reads it.
MIGRATIONS_PENDING_CODE = "migrations_pending"


class APIKeyBulkView(APIView):
    """
    Bulk endpoint for gateway startup key sync.
    Returns all active keys with their hashes so the gateway can restore
    its in-memory KeyStore on restart.

    Authenticated by admin token (not user JWT).
    """

    authentication_classes = []
    permission_classes = [IsAdminToken]
    renderer_classes = [JSONRenderer]  # bypass camelCase — Go expects snake_case
    _gm = GeneralMethods()

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                "X-Agentcc-Config-Key-Hashes",
                openapi.IN_HEADER,
                description="At most 32 comma-separated SHA-256 hex config-seed hashes (64 hex characters each).",
                type=openapi.TYPE_STRING,
                required=False,
            )
        ],
        responses={
            200: APIKeyBulkResponseSerializer,
            400: AgentccErrorResponseSerializer,
        },
    )
    @primary_required
    def get(self, request):
        header = request.headers.get("X-Agentcc-Config-Key-Hashes")
        hashes = (
            []
            if header is None
            else [value.strip().lower() for value in header.split(",")]
        )
        if len(hashes) > 32 or any(
            not re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes
        ):
            return self._gm.custom_error_response(
                status.HTTP_400_BAD_REQUEST,
                "X-Agentcc-Config-Key-Hashes requires at most 32 SHA-256 hex values",
                code="invalid_config_key_hashes",
            )
        # A new install's database has no key table until the bootstrap job has
        # migrated it. With bundled datastores the Helm chart runs that job as a
        # post-install hook, which `helm install --wait` starts only once the
        # gateway reports ready, and the gateway reports ready only once it
        # knows the app's keys. So say that the app has made no key, in a form
        # the gateway acts on (503 migrations_pending), before any query: the
        # SQL error of a missing table is never sent.
        if AgentccAPIKey._meta.db_table not in connection.introspection.table_names():
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "The database has no gateway key table yet: its migrations have not run.",
                code=MIGRATIONS_PENDING_CODE,
            )
        try:
            # Enumerate people independently of their API keys. A missing L1b
            # registry makes only delegation incomplete; older gateways ignore it.
            delegation = delegation_snapshot()
            loadable = gateway_loadable_keys()
            result = gateway_key_payloads(loadable)
            # _base_manager deliberately includes soft-deleted and every workspace.
            stored = AgentccAPIKey._base_manager.exclude(key_hash="")
            # PENDING is neither loadable nor a negative fact while the
            # personal identity still has authority. Exempt only that live
            # in-flight set, not deleted/expired/failed/authority-removed rows.
            retired = (
                stored.exclude(pk__in=loadable.values("pk"))
                .exclude(pk__in=gateway_inflight_keys().values("pk"))
                # Failed imports can have landed remotely without an ACK.
                # Keep their negative facts on EVERY pull, even after an outage
                # longer than the normal retirement horizon or a failed revoke.
                .filter(
                    Q(status=AgentccAPIKey.FAILED)
                    | Q(updated_at__gte=timezone.now() - timedelta(days=7))
                )
            )
            conflicts = (
                stored.annotate(normalized_hash=Lower("key_hash"))
                .filter(normalized_hash__in=hashes)
                .values_list("normalized_hash", flat=True)
            )
            response = self._gm.success_response(result)
            response.data.update(
                {
                    "snapshot": {"complete": True, "issued_ever": stored.count()},
                    "retired": [
                        {"id": key_id, "key_hash": key_hash.lower()}
                        for key_id, key_hash in retired.values_list(
                            "gateway_key_id", "key_hash"
                        )
                    ],
                    "config_conflicts": sorted(set(conflicts)),
                    "delegation": delegation,
                }
            )
            return response
        except Exception as e:
            logger.exception("api_key_bulk_error", error=str(e))
            return self._gm.internal_server_error_response("Internal server error")
