"""The virtual-server provisioning API for Omega EE (CN22a).

Two audiences, two kinds of caller:

- EE's service credential (``X-Api-Key`` and ``X-Secret-Key``) calls ensure,
  detach and the agent-key endpoints. Nothing else authenticates there: a
  person's token, the gateway admin token and the platform's own API keys
  (the org's ``system`` key included) get 403, and a missing, unknown,
  rotated-out or revoked pair gets 401. The credential is accepted nowhere
  else either, except one read: since CN22d the workspace's agent-key list
  (``views/ee_agent_key_policy.py``, through ``EEServiceCredentialIfPresent``),
  which EE reads to find a key whose mint answer it lost.
- An org Owner or Admin, signed in as a person, issues, rotates and revokes
  that credential. Its secret is shown once.

Answers use Django's envelope with the codes of ``omega-ee-attach/v1``
(spec section 3.5); a malformed request is ``invalid`` with ``attr`` naming
the field.
"""

from collections.abc import Mapping

from rest_framework import serializers, status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed,
    NotAuthenticated,
    PermissionDenied,
)
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView, exception_handler

from accounts.authentication import APIKeyAuthentication, custom_exception_handler
from agentcc.models.mcp_service_provisioning import (
    AgentccEEServiceCredential,
    AgentccEEVirtualServer,
)
from agentcc.permissions import IsOrgGatewayAdmin
from agentcc.serializers.contracts import (
    AgentccEmptyRequestSerializer,
    AgentccErrorResponseSerializer,
)
from agentcc.services import ee_agent_key_mint as mint_idempotency
from agentcc.services import mcp_service_provisioning as provisioning
from agentcc.services.ee_provisioning_availability import ProvisioningAvailabilityMixin
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope

NOT_EE_CREDENTIAL = (
    "Only this organization's EE service credential may call the provisioning API."
)
NO_CREDENTIAL = (
    "Send this organization's EE service credential as X-Api-Key and X-Secret-Key."
)
BAD_CREDENTIAL = (
    "The EE service credential is unknown, or it was rotated out or revoked."
)
PERSON_ONLY = (
    "Only an organization Owner or Admin, signed in, manages the EE service "
    "credential; an API key cannot."
)
SERVICE_REALM = 'ApiKey realm="agentcc-mcp-service"'
GENERIC_DETAIL_KEYS = frozenset({"detail", "error", "message", "non_field_errors"})


# --- Request and response shapes --------------------------------------------


class _Text(serializers.CharField):
    """A JSON string kept exactly as sent (no trimming, no number turned into
    text) and, with ``pattern``, matching it whole: Django's RegexValidator
    searches, and ``$`` also matches before a trailing newline."""

    default_error_messages = {"pattern": "Has the wrong form."}

    def __init__(self, pattern=None, **kwargs):
        kwargs.setdefault("trim_whitespace", False)
        super().__init__(**kwargs)
        self.pattern = pattern

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        value = super().to_internal_value(data)
        if self.pattern is not None and not self.pattern.fullmatch(value):
            self.fail("pattern")
        return value


_WORKSPACE_FORM = {"pattern": "Must be a workspace id (UUID)."}


class _AgentModeField(serializers.ChoiceField):
    """``mode``, declared agent only until CN22b builds user mode (CN22 R2,
    the reviewer's P3): the OpenAPI enum lists ``agent`` alone. A request for
    ``user`` still reaches ensure, which refuses it with the same 400
    ``invalid`` naming ``mode`` (USER_MODE_REFUSED); any other value is an
    invalid choice. The frozen CN5 contract keeps both values; CN22b widens
    this back to AgentccEEVirtualServer.Mode."""

    def __init__(self, **kwargs):
        agent = [
            c for c in AgentccEEVirtualServer.Mode.choices if c[0] == provisioning.AGENT
        ]
        super().__init__(choices=agent, **kwargs)

    def to_internal_value(self, data):
        if data == provisioning.USER:
            return provisioning.USER
        return super().to_internal_value(data)


class EEEnsureRequestSerializer(serializers.Serializer):
    """Spec section 3.2. ``agent_key_id`` is required in agent mode and
    refused in user mode."""

    workspace = _Text(
        provisioning.WORKSPACE_ID,
        error_messages=_WORKSPACE_FORM,
        help_text="Workspace id.",
    )
    connection_id = _Text(
        provisioning.CONNECTION_ID,
        error_messages={
            "pattern": "Must be 1 to 128 letters, digits and _ . : -, starting "
            "with a letter or digit."
        },
        help_text="EE's IntegrationConnection.id; with the workspace, the idempotency key.",
    )
    servers = serializers.ListField(
        child=_Text(),
        min_length=1,
        max_length=provisioning.MAX_SERVERS,
        help_text="1 to 8 MCP server ids of the org.",
    )
    mode = _AgentModeField()
    agent_key_id = _Text(required=False, help_text="key_ and 24 hex digits.")

    def validate(self, attrs):
        if len(set(attrs["servers"])) != len(attrs["servers"]):
            raise serializers.ValidationError({"servers": ["Name each server once."]})
        has_key = attrs.get("agent_key_id") is not None
        if attrs["mode"] == provisioning.AGENT and not has_key:
            raise serializers.ValidationError(
                {"agent_key_id": ["Agent mode needs agent_key_id."]}
            )
        if attrs["mode"] == provisioning.USER and has_key:
            raise serializers.ValidationError(
                {"agent_key_id": ["User mode takes no agent_key_id."]}
            )
        return attrs


class EEEnsureResultSerializer(serializers.Serializer):
    vs_id = serializers.RegexField(provisioning.VS_ID)
    url = serializers.CharField()
    mode = serializers.ChoiceField(
        choices=[
            c for c in AgentccEEVirtualServer.Mode.choices if c[0] == provisioning.AGENT
        ]
    )
    workspace = serializers.RegexField(provisioning.WORKSPACE_ID)
    connection_id = serializers.CharField()
    created = serializers.BooleanField()
    agent_key_expires_at = serializers.DateTimeField(
        allow_null=True,
        help_text="When the bound agent key expires (90 days after it was minted). "
        "EE mints a new key and ensures with it before then (CN22c).",
    )


class EEEnsureResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EEEnsureResultSerializer()


class EEDetachResultSerializer(serializers.Serializer):
    vs_id = serializers.RegexField(provisioning.VS_ID)
    connection_id = serializers.CharField()
    removed = serializers.BooleanField()


class EEDetachResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EEDetachResultSerializer()


class EEMintAgentKeyRequestSerializer(serializers.Serializer):
    workspace = _Text(
        provisioning.WORKSPACE_ID,
        error_messages=_WORKSPACE_FORM,
        help_text="Workspace id.",
    )


class EEMintAgentKeyResultSerializer(serializers.Serializer):
    agent_key_id = serializers.RegexField(provisioning.AGENT_KEY_ID)
    key = serializers.CharField(help_text="The raw key, returned this once.")
    workspace = serializers.RegexField(provisioning.WORKSPACE_ID)
    expires_at = serializers.DateTimeField(
        help_text="The key expires 90 days after it was minted (CN22c)."
    )


class EEMintAgentKeyResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EEMintAgentKeyResultSerializer()


class EEMintConflictResponseSerializer(AgentccErrorResponseSerializer):
    """409: ``agent_key_cap_reached`` (nothing made), or, since CN22d,
    ``agent_key_mint_replayed``: a mint with this Idempotency-Key already made
    the key named here, and its key is never shown again."""

    agent_key_id = serializers.RegexField(
        provisioning.AGENT_KEY_ID,
        required=False,
        help_text="agent_key_mint_replayed: the key the first mint made.",
    )
    created_at = serializers.DateTimeField(required=False)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    revoked = serializers.BooleanField(
        required=False,
        help_text="True once the key is revoked: EE then mints with a new "
        "Idempotency-Key.",
    )


class EERevokeAgentKeyResultSerializer(serializers.Serializer):
    agent_key_id = serializers.CharField()
    revoked = serializers.BooleanField()


class EERevokeAgentKeyResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EERevokeAgentKeyResultSerializer()


class EEServiceCredentialSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    api_key = serializers.CharField(help_text="Sent as X-Api-Key.")
    status = serializers.ChoiceField(choices=AgentccEEServiceCredential.Status.choices)
    created_at = serializers.DateTimeField()
    last_used_at = serializers.DateTimeField(allow_null=True)


class EEIssuedServiceCredentialSerializer(EEServiceCredentialSerializer):
    secret_key = serializers.CharField(
        help_text="Sent as X-Secret-Key. Shown this once; only its hash is kept."
    )


class EEServiceCredentialResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EEServiceCredentialSerializer(allow_null=True)


class EEIssuedServiceCredentialResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EEIssuedServiceCredentialSerializer()


class EERevokedServiceCredentialSerializer(EEServiceCredentialSerializer):
    agent_keys_revoked = serializers.ListField(
        child=serializers.CharField(),
        help_text="Every live agent key EE held in this organization, revoked "
        "with the credential.",
    )
    gateway_synced = serializers.BooleanField(
        help_text="False when the gateway could not be told yet; it drops "
        "those keys at its next key sync."
    )


class EERevokedServiceCredentialResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EERevokedServiceCredentialSerializer()


def _errors(*codes):
    return dict.fromkeys((*codes, 503), AgentccErrorResponseSerializer)


# --- Answers ----------------------------------------------------------------


def _error(exc: provisioning.ProvisioningError) -> Response:
    """A refusal; a 401 names the realm, as the authentication class's own
    401s do."""
    details = {exc.attr: [exc.message]} if exc.attr else None
    body = build_error_envelope(
        exc.message,
        status_code=exc.status,
        code=exc.code,
        details=details,
        extra=exc.extra,
    )
    headers = {"WWW-Authenticate": SERVICE_REALM} if exc.status == 401 else None
    return Response(body, status=exc.status, headers=headers)


def _messages(value, field: str, place: str = ""):
    """``(place, message)`` for each message under one field of a
    serializer's errors. A list child or a nested field adds its place
    (``servers[0]``) and every message is a plain string, never an
    ``ErrorDetail``'s repr."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            step = f"[{key}]" if str(key).isdigit() else f".{key}"
            yield from _messages(item, field, place + step)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _messages(item, field, place)
    else:
        yield (f"{field}{place}" if place else ""), str(value) or "Has the wrong form."


def _flat_errors(errors) -> list[tuple[str, str, list[str]]]:
    """Each field with its place-qualified messages and the first one's place
    and text: the contract's ``details`` holds one flat list of strings per
    field (schema.json $defs django_error_response)."""
    flat = []
    for field, value in errors.items():
        found = list(_messages(value, str(field))) or [("", "Has the wrong form.")]
        texts = [f"{place}: {text}" if place else text for place, text in found]
        flat.append((str(field), found[0], texts))
    return flat


def _invalid_request(errors) -> Response:
    """A request its serializer refused: 400 ``invalid``, ``attr`` naming the
    first field at fault and ``details`` every field's messages, each a
    plain string."""
    flat = _flat_errors(errors)
    named = [entry for entry in flat if entry[0] not in GENERIC_DETAIL_KEYS]
    field, (place, text), _ = (named or flat)[0]
    message = f"{place or field}: {text}" if named else text
    details = {name: texts for name, _, texts in flat}
    body = build_error_envelope(message, code="invalid", details=details)
    return Response(body, status=status.HTTP_400_BAD_REQUEST)


def _sync_failed(message: str) -> Response:
    return _error(provisioning.ProvisioningError(503, "gateway_sync_failed", message))


def _ok(result, *, created=False, secret=False) -> Response:
    """``secret``: the answer carries a secret shown this once, so no cache
    may keep it."""
    return Response(
        {"status": True, "result": result},
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        headers={"Cache-Control": "no-store"} if secret else None,
    )


def _envelope_exception_handler(exc, context):
    """DRF's refusals (401, 403, a body that is not JSON, a wrong method) in
    the contract's envelope: the code is the status's own (401
    not_authenticated, 403 permission_denied, 400 invalid), never DRF's
    (authentication_failed, parse_error), and there are no field
    ``details``. Anything else goes to the app's handler."""
    from accounts.api_key_policy import APIKeyRetired

    # Verified retirement guidance is shared across credential transports.
    # Other refusals keep this API's deliberately generic envelope.
    from agentcc.services.ee_provisioning_availability import ProvisioningUnavailable

    if isinstance(exc, APIKeyRetired | ProvisioningUnavailable) or not isinstance(
        exc, APIException
    ):
        return custom_exception_handler(exc, context)
    response = exception_handler(exc, context)
    if response is None:
        return None
    response.data = build_error_envelope(
        str(exc.detail) if isinstance(exc.detail, str) else "Request refused.",
        status_code=response.status_code,
        details={},
    )
    return response


# --- EE's side --------------------------------------------------------------


class ServicePrincipal:
    """Who calls with EE's service credential: EE for one org, never a
    person. Shaped enough like a user for the request middlewares."""

    is_authenticated = True
    is_anonymous = False
    is_active = True
    is_staff = False
    is_superuser = False

    def __init__(self, credential: AgentccEEServiceCredential):
        self.credential = credential
        self.organization = credential.organization
        self.organization_id = credential.organization_id
        self.id = self.pk = f"{provisioning.EE_OWNER}:{credential.api_key}"

    def __str__(self):
        return self.id


class EEServiceCredentialAuthentication(BaseAuthentication):
    """EE's service credential, and nothing else (AM-C18 Q1)."""

    def authenticate_header(self, request):
        return SERVICE_REALM

    def authenticate(self, request):
        if request.headers.get("Authorization"):
            raise PermissionDenied(NOT_EE_CREDENTIAL)
        api_key = request.headers.get("X-Api-Key") or ""
        secret = request.headers.get("X-Secret-Key") or ""
        if not api_key or not secret:
            raise NotAuthenticated(NO_CREDENTIAL)
        credential = provisioning.authenticate(api_key, secret)
        if credential is not None:
            return ServicePrincipal(credential), credential
        if provisioning.is_platform_api_key(api_key, secret):
            raise PermissionDenied(NOT_EE_CREDENTIAL)
        raise AuthenticationFailed(BAD_CREDENTIAL)


class EEServiceCredentialIfPresent(EEServiceCredentialAuthentication):
    """EE's service credential on a route a person may call too (CN22d: the
    workspace's agent-key list, which EE reads to find a key whose mint
    answer it lost). A request with no EE pair is left to the next
    authentication class; a pair that is not the org's current EE credential
    is too, and that class refuses it."""

    def authenticate_header(self, request):
        # The route's 401 challenge stays the platform's own (a person is the
        # usual caller), not the provisioning API's realm.
        return APIKeyAuthentication().authenticate_header(request)

    def authenticate(self, request):
        if request.headers.get("Authorization"):
            return None
        api_key = request.headers.get("X-Api-Key") or ""
        secret = request.headers.get("X-Secret-Key") or ""
        if not api_key or not secret:
            return None
        credential = provisioning.authenticate(api_key, secret)
        if credential is None:
            return None
        return ServicePrincipal(credential), credential


class IsEEServiceCredential(BasePermission):
    message = NOT_EE_CREDENTIAL

    def has_permission(self, request, view):
        return isinstance(request.auth, AgentccEEServiceCredential)


class _EnvelopeView(ProvisioningAvailabilityMixin, APIView):
    def get_exception_handler(self):
        return _envelope_exception_handler


class _ServiceView(_EnvelopeView):
    authentication_classes = [EEServiceCredentialAuthentication]
    permission_classes = [IsEEServiceCredential]


class EnsureVirtualServerView(_ServiceView):
    """POST mcp/service/virtual-servers/ensure/ (spec section 3.2)."""

    @validated_request(
        request_serializer=EEEnsureRequestSerializer,
        responses={200: EEEnsureResponseSerializer, **_errors(400, 401, 403, 404, 503)},
        reject_unknown_fields=True,
        validation_error_response=_invalid_request,
    )
    def post(self, request):
        if not provisioning.broker_enabled():
            return _error(provisioning.broker_off_error())
        data = request.validated_data
        req = provisioning.EnsureRequest(
            workspace=data["workspace"],
            connection_id=data["connection_id"],
            servers=tuple(data["servers"]),
            mode=data["mode"],
            agent_key_id=data.get("agent_key_id"),
        )
        try:
            outcome = provisioning.ensure(request.auth, req)
        except provisioning.ProvisioningError as exc:
            return _error(exc)
        if not outcome.synced:
            return _sync_failed(provisioning.ENSURE_NOT_SYNCED)
        row = outcome.row
        return _ok(
            {
                "vs_id": row.vs_id,
                "url": outcome.url,
                "mode": row.mode,
                "workspace": str(row.workspace_id),
                "connection_id": row.connection_id,
                "created": outcome.created,
                "agent_key_expires_at": outcome.agent_key_expires_at,
            }
        )


class DetachVirtualServerView(_ServiceView):
    """DELETE mcp/service/virtual-servers/<vs_id>/ (spec section 3.4)."""

    @validated_request(
        responses={200: EEDetachResponseSerializer, **_errors(400, 401, 403, 404, 503)}
    )
    def delete(self, request, vs_id):
        try:
            row, synced = provisioning.detach(request.auth, vs_id)
        except provisioning.ProvisioningError as exc:
            return _error(exc)
        if not synced:
            return _sync_failed(provisioning.DETACH_NOT_SYNCED)
        return _ok(
            {"vs_id": vs_id, "connection_id": row.connection_id, "removed": True}
        )


class MintAgentKeyView(_ServiceView):
    """POST mcp/service/agent-keys/ (spec section 3.7): the raw key, once.

    CN22d: an Idempotency-Key header (a UUIDv7) is required; a retry with
    the same one names the key the first mint made (409) instead of making
    another."""

    @validated_request(
        request_serializer=EEMintAgentKeyRequestSerializer,
        responses={
            201: EEMintAgentKeyResponseSerializer,
            409: EEMintConflictResponseSerializer,
            **_errors(400, 401, 403, 404, 422, 503),
        },
        reject_unknown_fields=True,
        validation_error_response=_invalid_request,
    )
    def post(self, request):
        if not provisioning.broker_enabled():
            return _error(provisioning.broker_off_error())
        try:
            idempotency_key = mint_idempotency.parse_key(
                request.headers.get(mint_idempotency.HEADER)
            )
        except mint_idempotency.InvalidKey as exc:
            return _error(
                provisioning.ProvisioningError(
                    400,
                    mint_idempotency.CODE_KEY_INVALID,
                    str(exc),
                    mint_idempotency.HEADER,
                )
            )
        try:
            binding, raw_key = provisioning.mint_agent_key(
                request.auth, request.validated_data["workspace"], idempotency_key
            )
        except provisioning.ProvisioningError as exc:
            return _error(exc)
        return _ok(
            {
                "agent_key_id": binding.api_key.gateway_key_id,
                "key": raw_key,
                "workspace": str(binding.workspace_id),
                "expires_at": binding.api_key.expires_at,
            },
            created=True,
            secret=True,
        )


class RevokeAgentKeyView(_ServiceView):
    """POST mcp/service/agent-keys/<agent_key_id>/revoke/ (spec section 3.7)."""

    @validated_request(
        request_serializer=AgentccEmptyRequestSerializer,
        responses={
            200: EERevokeAgentKeyResponseSerializer,
            **_errors(400, 401, 403, 404, 503),
        },
        reject_unknown_fields=True,
        validation_error_response=_invalid_request,
    )
    def post(self, request, agent_key_id):
        try:
            _, synced = provisioning.revoke_agent_key(request.auth, agent_key_id)
        except provisioning.ProvisioningError as exc:
            return _error(exc)
        if not synced:
            return _sync_failed(provisioning.REVOKE_NOT_SYNCED)
        return _ok({"agent_key_id": agent_key_id, "revoked": True})


# --- The org admin's side ---------------------------------------------------


class IsSignedInPerson(BasePermission):
    """A person's own session, never an API key: the credential this
    manages can mint the org's gateway keys, so a person answers for it."""

    message = PERSON_ONLY

    def has_permission(self, request, view):
        return getattr(request, "org_api_key", None) is None


def _describe(credential: AgentccEEServiceCredential) -> dict:
    return {
        "id": str(credential.id),
        "api_key": credential.api_key,
        "status": credential.status,
        "created_at": credential.created_at,
        "last_used_at": credential.last_used_at,
    }


def _issued(issued: provisioning.IssuedCredential) -> Response:
    """The new credential with its secret, which is never shown again."""
    return _ok(
        {**_describe(issued.credential), "secret_key": issued.secret},
        created=True,
        secret=True,
    )


class _AdminView(_EnvelopeView):
    permission_classes = [IsAuthenticated, IsSignedInPerson, IsOrgGatewayAdmin]


class ServiceCredentialView(_AdminView):
    """GET mcp/service/credential/: the current credential, never its
    secret (``null`` when there is none). POST: issue the first one."""

    @validated_request(
        responses={200: EEServiceCredentialResponseSerializer, **_errors(401, 403)}
    )
    def get(self, request):
        credential = provisioning.current_credential(request.organization)
        return _ok(_describe(credential) if credential is not None else None)

    @validated_request(
        request_serializer=AgentccEmptyRequestSerializer,
        responses={
            201: EEIssuedServiceCredentialResponseSerializer,
            **_errors(400, 401, 403, 409),
        },
        reject_unknown_fields=True,
        validation_error_response=_invalid_request,
    )
    def post(self, request):
        if not provisioning.broker_enabled():
            return _error(provisioning.broker_off_error())
        try:
            issued = provisioning.issue_credential(request.organization, request.user)
        except provisioning.ProvisioningError as exc:
            return _error(exc)
        return _issued(issued)


class RotateServiceCredentialView(_AdminView):
    """POST mcp/service/credential/rotate/: a new pair; the old one stops
    working at once."""

    @validated_request(
        request_serializer=AgentccEmptyRequestSerializer,
        responses={
            201: EEIssuedServiceCredentialResponseSerializer,
            **_errors(400, 401, 403, 404),
        },
        reject_unknown_fields=True,
        validation_error_response=_invalid_request,
    )
    def post(self, request):
        if not provisioning.broker_enabled():
            return _error(provisioning.broker_off_error())
        try:
            issued = provisioning.rotate_credential(request.organization, request.user)
        except provisioning.ProvisioningError as exc:
            return _error(exc)
        return _issued(issued)


class RevokeServiceCredentialView(_AdminView):
    """POST mcp/service/credential/revoke/: EE is out for good. Every live
    agent key it held in the org is revoked too, so EE can neither provision
    nor call a virtual server until an admin issues a new credential and EE
    mints new keys. The answer is 200 even when the gateway could not be told
    (``gateway_synced`` false): a repeat would find no credential, and the
    gateway drops those keys at its next key sync."""

    @validated_request(
        request_serializer=AgentccEmptyRequestSerializer,
        responses={
            200: EERevokedServiceCredentialResponseSerializer,
            **_errors(400, 401, 403, 404),
        },
        reject_unknown_fields=True,
        validation_error_response=_invalid_request,
    )
    def post(self, request):
        try:
            revoked = provisioning.revoke_credential(request.organization, request.user)
        except provisioning.ProvisioningError as exc:
            return _error(exc)
        return _ok(
            {
                **_describe(revoked.credential),
                "agent_keys_revoked": list(revoked.agent_key_ids),
                "gateway_synced": revoked.gateway_synced,
            }
        )
