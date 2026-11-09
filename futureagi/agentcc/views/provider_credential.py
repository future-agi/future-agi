from json import loads

import requests as http_requests
import structlog
from django.db import transaction
from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from requests.exceptions import HTTPError, InvalidHeader
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.permissions import GatewayAdminActionsMixin
from agentcc.serializers.provider_credential import (
    AgentccProviderCredentialCreateSerializer,
    AgentccProviderCredentialSerializer,
    AgentccProviderCredentialUpdateSerializer,
    unsupported_credential_keys,
    unsupported_credential_keys_message,
)
from agentcc.services.config_push import push_org_config
from agentcc.services.managed_models_default import initial_managed_models
from agentcc.services.org_config_writer import lock_org
from agentcc.services.url_safety import (
    PROVIDER_PRIVATE_URL_ERROR,
    PROVIDER_URL_ERROR,
    build_ssrf_safe_session,
    ensure_provider_base_url_allowed,
    ensure_public_http_url,
    private_provider_urls_allowed,
)
from agentcc.views._errors import ProviderAuthenticationRejected, exception_response
from integrations.services.credentials import CredentialManager
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

_GATEWAY_SYNC_WARNING = (
    "Config saved but gateway sync failed. Changes will apply on next gateway restart."
)

_DEFAULT_API_PATH_PREFIX = "/v1"

# fetch_models answers any org member. A key that cannot go in a header makes
# requests (InvalidHeader) or http.client (ValueError) fail with a message that
# quotes the whole header value, key included, so such a key is refused with
# one of these, which never carry it.
_MALFORMED_SAVED_KEY = (
    "The saved provider credential is malformed (it has surrounding whitespace "
    "or a line break); rotate it."
)
_MALFORMED_REQUEST_KEY = (
    "The API key is malformed (it has surrounding whitespace or a line break)."
)


def _malformed_key(api_key):
    """Whether api_key would be refused as (part of) an HTTP header value."""
    return api_key != api_key.strip() or not api_key.isprintable()


_GOOGLE_ERROR_MAX_BYTES = 8192
_GOOGLE_ERROR_MAX_DEPTH = 8
_GOOGLE_ERROR_MAX_CONTAINERS = 128
_GOOGLE_ERROR_MAX_DETAILS = 16


def _bounded_google_error(response):
    """Best-effort, bounded inspection of an already-received error document."""
    # Requests' public content/text/json accessors may consume a pending stream
    # or sniff/replace encodings. Only inspect bytes already buffered by get().
    body = response._content
    if not isinstance(body, bytes) or not body or len(body) > _GOOGLE_ERROR_MAX_BYTES:
        return None
    encoding = response.encoding
    if encoding is not None and (
        not isinstance(encoding, str) or encoding.lower() not in ("utf-8", "utf8")
    ):
        return None

    # Bound decoder work before decoding, including ignored fields. JSON's
    # delimiters are ASCII; escaped quotes and delimiters inside strings do not
    # count. Full syntax validation is still the JSON decoder's responsibility.
    depth = containers = 0
    quoted = escaped = False
    for byte in body:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:  # backslash
                escaped = True
            elif byte == 34:  # quote
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (123, 91):  # object or array
            depth += 1
            containers += 1
            if (
                depth > _GOOGLE_ERROR_MAX_DEPTH
                or containers > _GOOGLE_ERROR_MAX_CONTAINERS
            ):
                return None
        elif byte in (125, 93):
            depth -= 1
            if depth < 0:
                return None
    if quoted or depth:
        return None
    try:
        return loads(body.decode("utf-8"))
    except (ValueError, RecursionError):
        # Includes strict UTF-8 decoding errors. Never attach parser details or
        # catch fatal/process-control exceptions; refusal keeps the HTTPError.
        return None


def _google_key_rejected(response):
    """Recognize only the audited Google HTTP400 ErrorInfo credential code."""
    if response.status_code != 400:
        return False
    payload = _bounded_google_error(response)
    error = payload.get("error") if isinstance(payload, dict) else None
    details = error.get("details") if isinstance(error, dict) else None
    if not isinstance(details, list) or len(details) > _GOOGLE_ERROR_MAX_DETAILS:
        return False
    return any(
        isinstance(detail, dict)
        and detail.get("@type") == "type.googleapis.com/google.rpc.ErrorInfo"
        and detail.get("domain") == "googleapis.com"
        and detail.get("reason") == "API_KEY_INVALID"
        for detail in details
    )


def _join_api_endpoint(base_url, api_path_prefix, path):
    """Mirror of the gateway's ``config.JoinEndpoint``.

    ``None`` means the provider states no prefix and the default applies; ``""``
    means it states an empty one. A base_url already ending in the prefix keeps
    it rather than repeating it.
    """
    base = (base_url or "").rstrip("/")
    if api_path_prefix is None:
        prefix = _DEFAULT_API_PATH_PREFIX
    else:
        prefix = str(api_path_prefix).strip().rstrip("/")
        if prefix and not prefix.startswith("/"):
            prefix = "/" + prefix
    if not prefix or base.endswith(prefix):
        return base + path
    return base + prefix + path


class AgentccProviderCredentialViewSet(
    GatewayAdminActionsMixin, BaseModelViewSetMixinWithUserOrg, ModelViewSet
):
    permission_classes = [IsAuthenticated]
    # fetch_models only reads through the saved key, so members keep it.
    gateway_admin_actions = frozenset(
        {"create", "update", "partial_update", "destroy", "rotate"}
    )
    serializer_class = AgentccProviderCredentialSerializer
    queryset = AgentccProviderCredential.no_workspace_objects.all()
    _gm = GeneralMethods()

    def get_serializer_class(self):
        # DRF and schema generation use the same input serializer. Read paths
        # keep the masked model representation rather than the write payload.
        if self.action == "create":
            return AgentccProviderCredentialCreateSerializer
        if self.action in {"update", "partial_update"}:
            return AgentccProviderCredentialUpdateSerializer
        return AgentccProviderCredentialSerializer

    def get_queryset(self):
        organization = getattr(self.request, "organization", None)
        queryset = AgentccProviderCredential.no_workspace_objects.filter(deleted=False)
        if organization is None:
            return queryset.none()

        queryset = queryset.filter(organization=organization)
        provider = self.request.query_params.get("provider_name")
        if provider:
            queryset = queryset.filter(provider_name=provider)
        return queryset

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()
            serializer = AgentccProviderCredentialSerializer(queryset, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            return exception_response(
                self._gm, e, logger=logger, event="provider_credential_list_error"
            )

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            return self._gm.success_response(
                AgentccProviderCredentialSerializer(instance).data
            )
        except Exception as e:
            logger.exception("provider_credential_retrieve_error", error=str(e))
            return self._gm.not_found("Provider credential not found")

    @swagger_auto_schema(responses={201: AgentccProviderCredentialSerializer})
    def create(self, request, *args, **kwargs):
        try:
            serializer = self.get_serializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            data = serializer.validated_data
            org = getattr(request, "organization", None)
            if org is None:
                return self._gm.bad_request("Organization context is required")

            existing = AgentccProviderCredential.no_workspace_objects.filter(
                organization=org,
                provider_name=data["provider_name"],
                deleted=False,
            ).first()
            if existing:
                return self._gm.bad_request(
                    f"Provider '{data['provider_name']}' already has a credential. "
                    f"Use PATCH to update or rotate."
                )
            try:
                ensure_provider_base_url_allowed(data.get("base_url", ""))
            except ValueError as e:
                return self._gm.bad_request(str(e))

            encrypted = CredentialManager.encrypt(data["credentials"])

            credential = AgentccProviderCredential.no_workspace_objects.create(
                organization=org,
                provider_name=data["provider_name"],
                display_name=data.get("display_name", ""),
                encrypted_credentials=encrypted,
                base_url=data.get("base_url", ""),
                api_format=data.get("api_format", "openai"),
                models_list=data.get("models_list", []),
                default_timeout_seconds=data.get("default_timeout_seconds", 60),
                max_concurrent=data.get("max_concurrent", 100),
                conn_pool_size=data.get("conn_pool_size", 100),
                extra_config=data.get("extra_config", {}),
            )

            synced = self._push_config_to_gateway(org)

            data = AgentccProviderCredentialSerializer(credential).data
            data["gateway_synced"] = synced
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.create_response(data)
        except Exception as e:
            return exception_response(
                self._gm, e, logger=logger, event="provider_credential_create_error"
            )

    @swagger_auto_schema(responses={200: AgentccProviderCredentialSerializer})
    def update(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)

    @swagger_auto_schema(responses={200: AgentccProviderCredentialSerializer})
    def partial_update(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance, data=request.data, partial=True)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            try:
                ensure_provider_base_url_allowed(
                    serializer.validated_data.get("base_url", instance.base_url),
                    saved_base_url=instance.base_url,
                )
            except ValueError as e:
                return self._gm.bad_request(str(e))

            safe_fields = {
                "display_name",
                "base_url",
                "api_format",
                "models_list",
                "default_timeout_seconds",
                "max_concurrent",
                "conn_pool_size",
                "extra_config",
                "is_active",
            }
            update_fields = ["updated_at"]
            for field, value in serializer.validated_data.items():
                if field in safe_fields:
                    setattr(instance, field, value)
                    update_fields.append(field)
            instance.save(update_fields=update_fields)

            synced = self._push_config_to_gateway(instance.organization)

            data = AgentccProviderCredentialSerializer(instance).data
            data["gateway_synced"] = synced
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            return exception_response(
                self._gm, e, logger=logger, event="provider_credential_update_error"
            )

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            instance.deleted = True
            instance.deleted_at = timezone.now()
            instance.save(update_fields=["deleted", "deleted_at", "updated_at"])
            synced = self._push_config_to_gateway(instance.organization)
            data = {"deleted": True, "gateway_synced": synced}
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            return exception_response(
                self._gm, e, logger=logger, event="provider_credential_delete_error"
            )

    @action(detail=True, methods=["post"])
    def rotate(self, request, pk=None):
        """Rotate credentials — accepts new credentials, encrypts, and updates."""
        try:
            instance = self.get_object()
            credentials = request.data.get("credentials")
            if not credentials or not isinstance(credentials, dict):
                return self._gm.bad_request(
                    "credentials must be a JSON object with at least an 'api_key' field"
                )
            if "api_key" not in credentials:
                return self._gm.bad_request("credentials must contain 'api_key'")
            # Trimmed as create's serializer field (CharField) trims them: a
            # key saved with a trailing newline fails in every header it goes in.
            credentials = {
                name: value.strip() if isinstance(value, str) else value
                for name, value in credentials.items()
            }
            # NG-58: the new credentials replace the stored ones, so a key the
            # provider contract does not take would drop the provider from
            # every push; refuse it and keep the stored credentials.
            unsupported = unsupported_credential_keys(
                instance.provider_name, credentials
            )
            if unsupported:
                return self._gm.bad_request(
                    unsupported_credential_keys_message(
                        instance.provider_name, unsupported
                    )
                )

            instance.encrypted_credentials = CredentialManager.encrypt(credentials)
            instance.last_rotated_at = timezone.now()
            instance.save(
                update_fields=[
                    "encrypted_credentials",
                    "last_rotated_at",
                    "updated_at",
                ]
            )

            synced = self._push_config_to_gateway(instance.organization)

            data = AgentccProviderCredentialSerializer(instance).data
            data["gateway_synced"] = synced
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            return exception_response(
                self._gm, e, logger=logger, event="provider_credential_rotate_error"
            )

    @action(detail=False, methods=["post"])
    def fetch_models(self, request):
        """Fetch available models from a provider's API.

        Two modes:
        - provider_name: look up stored credential by provider name for this org.
        - api_key + base_url + api_format: use raw values (for create-mode).
        """
        provider_name = request.data.get("provider_name")
        api_key = None
        base_url = None
        api_format = None
        api_path_prefix = None
        saved_base_url = None

        if provider_name:
            organization = getattr(request, "organization", None)
            if organization is None:
                return self._gm.bad_request("Organization context is required")

            cred = AgentccProviderCredential.no_workspace_objects.filter(
                provider_name=provider_name,
                organization=organization,
                deleted=False,
            ).first()
            if cred:
                try:
                    decrypted = CredentialManager.decrypt(cred.encrypted_credentials)
                except Exception:
                    logger.warning(
                        "provider_credential_decrypt_failed",
                        provider_name=provider_name,
                        organization_id=str(organization.id),
                        exc_info=True,
                    )
                    return self._gm.bad_request(
                        "Saved provider credential could not be decrypted. "
                        "Rotate the credential or check the encryption configuration."
                    )
                api_key = decrypted.get("api_key", "")
                base_url = cred.base_url.rstrip("/") if cred.base_url else ""
                saved_base_url = base_url
                api_format = cred.api_format
                api_path_prefix = (cred.extra_config or {}).get("api_path_prefix")

        # The saved key only ever goes where it was saved for: a caller who can
        # use the credential but not read it must not be able to redirect it.
        uses_saved_key = saved_base_url is not None and not request.data.get("api_key")
        requested_base_url = (request.data.get("base_url") or "").rstrip("/")
        if (
            uses_saved_key
            and requested_base_url
            and requested_base_url != saved_base_url
        ):
            return self._gm.bad_request(
                "The saved API key is only sent to the provider's saved base URL. "
                "Provide an api_key to fetch models from a different one."
            )

        # Raw values from request body override or fill gaps.
        base_url = (requested_base_url or base_url or "").rstrip("/")
        api_key = request.data.get("api_key") or api_key or ""
        # The format decides the discovery URL and how the key is sent (the
        # google branch puts it in the query string), so a saved key keeps the
        # format it was saved with.
        if not uses_saved_key:
            api_format = request.data.get("api_format") or api_format
        api_format = api_format or "openai"
        # Presence, not truthiness: "" is an explicit "no version segment", and
        # an `or` chain would silently swap it for the stored value.
        if "api_path_prefix" in request.data:
            api_path_prefix = request.data.get("api_path_prefix")

        if not api_key:
            msg = (
                f"No saved credential found for provider '{provider_name}'. "
                "Provide an api_key to fetch models."
                if provider_name
                else "api_key is required"
            )
            return self._gm.bad_request(msg)

        malformed = _MALFORMED_SAVED_KEY if uses_saved_key else _MALFORMED_REQUEST_KEY
        if isinstance(api_key, str) and _malformed_key(api_key):
            return self._gm.bad_request(malformed)

        try:
            models = self._fetch_models_from_provider(
                provider_name, base_url, api_key, api_format, api_path_prefix
            )
            return self._gm.success_response({"models": models})
        except InvalidHeader:
            # A ValueError too, whose message quotes the header value: never
            # answer with it.
            return self._gm.bad_request(malformed)
        except Exception as e:
            # InvalidURL/MissingSchema/JSONDecodeError are ValueErrors too.
            # Only these exact validator messages are public; transport text
            # and tracebacks can contain the saved Google key in ?key=.
            response = exception_response(
                self._gm,
                e,
                logger=logger,
                event="fetch_models_error",
                safe_messages=(
                    PROVIDER_URL_ERROR,
                    PROVIDER_PRIVATE_URL_ERROR,
                    "Connection to private address blocked",
                ),
                log_detail=False,
            )
            if response.data["code"] == "provider_authentication_failed":
                # A definitive key refusal is a discovery verdict, not a failed
                # probe. Keep the dialog's rejected-key Save guard and manual
                # model override. Transport/5xx failures remain no-verdict errors.
                return self._gm.success_response(
                    {
                        "models": [],
                        "error": response.data["result"],
                        "code": response.data["code"],
                    }
                )
            return response

    def _fetch_models_from_provider(
        self, provider_name, base_url, api_key, api_format, api_path_prefix=None
    ):
        """Call the provider's list-models endpoint and return a sorted list of model IDs.

        Listing models is an identity lookup, not a translation call — so it uses
        the provider's *native* protocol regardless of ``api_format`` (which
        only describes how the gateway translates at runtime). Branches on
        ``provider_name`` first; falls back to ``api_format`` for custom /
        unknown providers.
        """
        timeout = 15

        # Validate a user-supplied base_url, then call it through the safe
        # session: it checks every address the host resolves to and connects
        # to the address it checked, on every redirect hop, so a DNS answer
        # that changes after the check (rebinding) is never dialed.
        if base_url:
            allow_private = private_provider_urls_allowed()
            ensure_public_http_url(
                base_url,
                PROVIDER_URL_ERROR,
                allow_private=allow_private,
                private_message=PROVIDER_PRIVATE_URL_ERROR,
            )
            http = build_ssrf_safe_session(
                "Connection to private address blocked", allow_private=allow_private
            )
        else:
            http = http_requests

        name = (provider_name or "").lower()

        if name == "anthropic" or api_format == "anthropic":
            # Anthropic's catalogue is always at /v1/models, so state "/v1" as
            # the prefix rather than concatenating it: a base URL that already
            # carries one must not produce "/v1/v1/models", and a trailing
            # slash must not produce "//v1/models". Reachable from a custom
            # provider, which can select the anthropic API format.
            url = _join_api_endpoint(
                base_url or "https://api.anthropic.com",
                _DEFAULT_API_PATH_PREFIX,
                "/models",
            )
            resp = http.get(
                url,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return sorted(m["id"] for m in data.get("data", []))

        if name in ("google", "gemini", "google_gemini") or api_format in (
            "gemini",
            "google",
        ):
            # base_url is a Gemini-protocol host only in the google/gemini
            # formats. The Google preset can also be saved in the openai format
            # against Google's OpenAI-compatible base (".../v1beta/openai"); its
            # key is still a Google key, so it is listed at Google's own host.
            gemini_base_url = base_url if api_format in ("gemini", "google") else ""
            # No prefix to de-duplicate: the gateway's Gemini provider always
            # appends "/v1beta" to the base URL (gemini/translate.go buildURL),
            # so a base already ending in it fails there and must fail here.
            url = _join_api_endpoint(
                gemini_base_url or "https://generativelanguage.googleapis.com",
                "",
                "/v1beta/models",
            )
            resp = http.get(
                url,
                params={"key": api_key},
                timeout=timeout,
            )
            try:
                resp.raise_for_status()
            except HTTPError:
                # Only the Google protocol's structured credential refusal is
                # a key verdict. Unknown/malformed 400s keep their HTTPError.
                if _google_key_rejected(resp):
                    raise ProviderAuthenticationRejected from None
                raise
            data = resp.json()
            models = []
            for m in data.get("models", []):
                mname = m.get("name", "")
                # Strip "models/" prefix
                if mname.startswith("models/"):
                    mname = mname[len("models/") :]
                if mname:
                    models.append(mname)
            return sorted(models)

        if name == "cohere":
            # Cohere native: /v1/models with Bearer auth. Stating "/v1" as the
            # prefix subsumes the old "/compatibility/v1" special case — that
            # base already ends in the prefix, so it lands on the identical URL
            # — and stops any other base ending in "/v1" from doubling it.
            url = _join_api_endpoint(
                base_url or "https://api.cohere.com",
                _DEFAULT_API_PATH_PREFIX,
                "/models",
            )
            resp = http.get(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            # Cohere returns {"models": [{"name": "..."}]}; compat returns OpenAI shape.
            if "data" in data:
                return sorted(m["id"] for m in data["data"])
            return sorted(m["name"] for m in data.get("models", []) if m.get("name"))

        # Default: OpenAI-compatible (OpenAI, Groq, Together, Fireworks, Mistral,
        # Azure OpenAI-compat, custom/self-hosted). The version segment is the
        # provider's own, so discovery probes the URL the proxy will really use.
        url = _join_api_endpoint(
            base_url or "https://api.openai.com/v1", api_path_prefix, "/models"
        )
        resp = http.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return sorted(m["id"] for m in data.get("data", []))

    def _push_config_to_gateway(self, org):
        """Returns True if gateway was updated, False if push failed."""
        try:
            with transaction.atomic():
                # Org lock first, as every config write: an org without a
                # config gets version 1 here. The push runs under the lock,
                # as write_config_version's does, so a writer cannot push a
                # newer version between this read and this push.
                lock_org(org.pk)
                config = AgentccOrgConfig.no_workspace_objects.filter(
                    organization=org, is_active=True, deleted=False
                ).first()
                if not config:
                    # A new org's first version gets Future AGI models on where
                    # the deployment offers them, as every first version does
                    # (managed_models_default), so adding a provider first does
                    # not leave them off for its first key.
                    config = AgentccOrgConfig.no_workspace_objects.create(
                        organization=org,
                        version=1,
                        is_active=True,
                        managed_models=initial_managed_models(org),
                    )
                return push_org_config(str(org.id), config)
        except Exception as e:
            logger.warning("config_push_failed", org_id=str(org.id), error=str(e))
            return False
