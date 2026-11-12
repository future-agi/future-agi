from json import loads

import requests as http_requests
import structlog
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
from agentcc.services.provider_model_safety import (
    InvalidProviderCredentialContext,
    credential_context_strings,
    credential_text_leaves,
    filter_provider_model_ids,
    retires_credential_text,
)
from agentcc.services.provider_public_projection import public_provider_models
from agentcc.services.provider_secret_policy import extra_config_secret_values
from agentcc.services.provider_url_auth import provider_url_auth_values
from agentcc.services.url_safety import (
    PROVIDER_PRIVATE_URL_ERROR,
    PROVIDER_URL_ERROR,
    build_ssrf_safe_session,
    ensure_provider_base_url_allowed,
    ensure_public_http_url,
    private_provider_urls_allowed,
)
from agentcc.views._errors import (
    ProviderAuthenticationRejected,
    exception_response,
)
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from integrations.services.credentials import CredentialManager
from requests.exceptions import HTTPError, InvalidHeader
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

_GATEWAY_SYNC_WARNING = "Config saved but gateway sync failed. A gateway connected to this app retries fetching saved configs during its periodic sync."

_DEFAULT_API_PATH_PREFIX = "/v1"
PROVIDER_REDIRECT_MESSAGE = (
    "Models could not be loaded because the provider returned an HTTP 3xx response, "
    "and redirects are not followed. Check the Base URL and API Path Prefix."
)


class _ProviderRedirectRefused(Exception):
    """Value-free refusal: never attach the upstream response or Location."""


def _refuse_provider_redirect(response):
    if 300 <= response.status_code < 400:
        raise _ProviderRedirectRefused


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


def _blank_api_key(credentials):
    if not isinstance(credentials, dict):
        return False
    api_key = credentials.get("api_key")
    return isinstance(api_key, str) and not api_key.strip()


def _invalid_credential_values(credentials):
    return isinstance(credentials, dict) and any(
        not isinstance(value, str) for value in credentials.values()
    )


def _without_blank_credentials(credentials):
    return {
        name: value
        for name, value in credentials.items()
        if not (isinstance(value, str) and not value.strip())
    }


def _public_provider_data(instance, credentials=None, prior_credentials=()):
    """All public projections use the same fail-closed serializer boundary."""
    context = {"prior_model_credentials": prior_credentials}
    if credentials is not None:
        context["model_credentials"] = credentials
    return AgentccProviderCredentialSerializer(instance, context=context).data


def _settings_are_text(decrypted, cred):
    """A saved credential that decrypts to a mapping whose settings (secret
    extra_config values and URL auth) are all text, so only the credential
    itself can hold a value that is not text."""
    if not isinstance(decrypted, dict):
        return False
    try:
        credential_context_strings(
            (
                extra_config_secret_values(cred.extra_config),
                provider_url_auth_values(cred.base_url),
            )
        )
    except Exception:
        return False
    return True


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


# The api_format of a structured-decision provider (TypeSafe, Perplexity
# Decider, a custom System One endpoint), and the gateway's alias for it.
_SYSTEM_ONE_API_FORMATS = ("systemone", "typesafe")


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
        if getattr(self, "action", None) in {"rotate", "update", "partial_update"}:
            queryset = queryset.select_for_update()
        provider = self.request.query_params.get("provider_name")
        if provider:
            queryset = queryset.filter(provider_name=provider)
        return queryset

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()
            return self._gm.success_response(
                [_public_provider_data(instance) for instance in queryset]
            )
        except Exception as e:
            return exception_response(
                self._gm, e, logger=logger, event="provider_credential_list_error"
            )

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            return self._gm.success_response(_public_provider_data(instance))
        except Exception as e:
            logger.exception("provider_credential_retrieve_error", error=str(e))
            return self._gm.not_found("Provider credential not found")

    @swagger_auto_schema(responses={201: AgentccProviderCredentialSerializer})
    def create(self, request, *args, **kwargs):
        try:
            if _invalid_credential_values(request.data.get("credentials")):
                return self._gm.bad_request("Credential values must be strings.")
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

            projected = filter_provider_model_ids(
                data.get("models_list", []),
                (
                    data["credentials"],
                    provider_url_auth_values(data.get("base_url", "")),
                    extra_config_secret_values(data.get("extra_config", {})),
                ),
                provider_name=data["provider_name"],
                api_format=data.get("api_format", "openai"),
                enforce_discovery_grammar=False,
            )
            encrypted = CredentialManager.encrypt(
                _without_blank_credentials(data["credentials"])
            )

            credential = AgentccProviderCredential(
                organization=org,
                provider_name=data["provider_name"],
                display_name=data.get("display_name", ""),
                encrypted_credentials=encrypted,
                base_url=data.get("base_url", ""),
                api_format=data.get("api_format", "openai"),
                models_list=projected["models"],
                default_timeout_seconds=data.get("default_timeout_seconds", 60),
                max_concurrent=data.get("max_concurrent", 100),
                conn_pool_size=data.get("conn_pool_size", 100),
                extra_config=data.get("extra_config", {}),
            )
            credential.disable_auto_workspace_assignment = True
            credential.save(force_insert=True)

            synced = self._push_config_to_gateway(org)

            data = _public_provider_data(credential, data["credentials"])
            if projected["dropped_model_count"]:
                data["dropped_model_count"] = projected["dropped_model_count"]
            data["gateway_synced"] = synced
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.create_response(data)
        except InvalidProviderCredentialContext:
            return self._gm.custom_error_response(
                400,
                str(InvalidProviderCredentialContext()),
                code=InvalidProviderCredentialContext.code,
            )
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
            with transaction.atomic():
                return self._partial_update_locked(request, *args, **kwargs)
        except Exception as e:
            return exception_response(
                self._gm, e, logger=logger, event="provider_credential_update_error"
            )

    def _partial_update_locked(self, request, *args, **kwargs):
        try:
            org = getattr(request, "organization", None)
            if org is None:
                return self._gm.bad_request("Organization context is required")
            # Prevent a stale metadata writer from restoring pre-rotation IDs.
            lock_org(org.pk)
            instance = self.get_object()
            # PUT/PATCH remain metadata-only; an explicit blank key is still a
            # refused credential write, not a successful no-op followed by push.
            if _blank_api_key(request.data.get("credentials")):
                return self._gm.bad_request("credentials.api_key must not be blank")
            if _invalid_credential_values(request.data.get("credentials")):
                return self._gm.bad_request("Credential values must be strings.")
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

            # Every save may push retained IDs, even a display-name-only edit.
            try:
                credentials = (
                    CredentialManager.decrypt(bytes(instance.encrypted_credentials))
                    if instance.encrypted_credentials
                    else {}
                )
                if not isinstance(credentials, dict):
                    raise ValueError
            except Exception:
                return self._gm.custom_error_response(
                    400,
                    "Saved provider credential could not be decrypted. "
                    "Repair the encryption configuration or submit a complete "
                    "credential set with replace_credentials=true to replace it.",
                    code=InvalidProviderCredentialContext.code,
                )
            # A list sent back as the console offered it (any order) is no
            # change: the saved IDs stay, the ones the reads hold back as well,
            # unless the edit drops a secret value (URL auth, a secret setting)
            # an ID could spell.
            submitted = serializer.validated_data.get("models_list")
            offered = public_provider_models(instance, credentials=credentials)
            unchanged = submitted is None or set(submitted) == set(offered["models"])
            keep_saved = unchanged and not retires_credential_text(
                (
                    provider_url_auth_values(instance.base_url),
                    extra_config_secret_values(instance.extra_config),
                ),
                (
                    provider_url_auth_values(
                        serializer.validated_data.get("base_url", instance.base_url)
                    ),
                    extra_config_secret_values(
                        serializer.validated_data.get(
                            "extra_config", instance.extra_config
                        )
                    ),
                ),
            )
            projected = filter_provider_model_ids(
                instance.models_list if unchanged else submitted,
                (
                    credentials,
                    provider_url_auth_values(instance.base_url),
                    provider_url_auth_values(
                        serializer.validated_data.get("base_url", instance.base_url)
                    ),
                    extra_config_secret_values(instance.extra_config),
                    extra_config_secret_values(
                        serializer.validated_data.get("extra_config", {})
                    ),
                ),
                provider_name=instance.provider_name,
                api_format=serializer.validated_data.get(
                    "api_format", instance.api_format
                ),
                enforce_discovery_grammar=False,
            )
            serializer.validated_data["models_list"] = (
                instance.models_list if keep_saved else projected["models"]
            )
            dropped_models = projected["dropped_model_count"]

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

            data = _public_provider_data(instance)
            if dropped_models:
                data["dropped_model_count"] = dropped_models
            data["gateway_synced"] = synced
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except InvalidProviderCredentialContext:
            return self._gm.custom_error_response(
                400,
                str(InvalidProviderCredentialContext()),
                code=InvalidProviderCredentialContext.code,
            )
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
        """Merge credentials, or explicitly replace the entire credential set."""
        try:
            credentials = request.data.get("credentials")
            if not credentials or not isinstance(credentials, dict):
                return self._gm.bad_request(
                    "credentials must be a JSON object with at least an 'api_key' field"
                )
            if "api_key" not in credentials:
                return self._gm.bad_request("credentials must contain 'api_key'")
            if _invalid_credential_values(credentials):
                return self._gm.bad_request("Credential values must be strings.")
            if _blank_api_key(credentials):
                return self._gm.bad_request("credentials.api_key must not be blank")
            replace = request.data.get("replace_credentials", False)
            if not isinstance(replace, bool):
                return self._gm.bad_request("replace_credentials must be a boolean.")
            # Match create's trimming, but never coerce non-string secrets.
            credentials = {name: value.strip() for name, value in credentials.items()}
            org = getattr(request, "organization", None)
            if org is None:
                return self._gm.bad_request("Organization context is required")

            with transaction.atomic():
                # Every config writer takes the org lock first. get_queryset
                # locks the scoped credential row before its ciphertext is read.
                lock_org(org.pk)
                instance = self.get_object()
                unsupported = unsupported_credential_keys(
                    instance.provider_name, credentials
                )
                if unsupported:
                    return self._gm.bad_request(
                        unsupported_credential_keys_message(
                            instance.provider_name, unsupported
                        )
                    )

                if replace and any(not value for value in credentials.values()):
                    return self._gm.bad_request(
                        "Full replacement requires nonblank credential values."
                    )
                # prior_unreadable: the saved credential cannot be decrypted (or
                # its settings are not text, or its text cannot be read in
                # bounds): replacement is still allowed, but no legacy ID can be
                # certified without the prior exclusion context, so the saved
                # IDs are cleared. prior_non_text: it decrypts but holds a value
                # that is not text: a replacement screens the saved IDs against
                # the new credential and the old one's text, and keeps them.
                prior_unreadable = prior_non_text = False
                try:
                    stored = (
                        CredentialManager.decrypt(bytes(instance.encrypted_credentials))
                        if instance.encrypted_credentials
                        else {}
                    )
                    if not isinstance(stored, dict):
                        raise InvalidProviderCredentialContext
                    credential_context_strings(
                        (
                            extra_config_secret_values(instance.extra_config),
                            provider_url_auth_values(instance.base_url),
                        )
                    )
                except Exception:
                    prior_unreadable = True
                    stored = {}
                prior = stored
                if not prior_unreadable:
                    try:
                        credential_context_strings(stored)
                    except Exception:
                        try:
                            prior = credential_text_leaves(stored)
                            prior_non_text = True
                        except Exception:
                            prior_unreadable = True
                            stored = prior = {}
                if prior_unreadable or prior_non_text:
                    if not replace:
                        return self._gm.custom_error_response(
                            400,
                            "Saved provider credential could not be decrypted or validated. "
                            "Repair the encryption configuration or submit a complete "
                            "credential set with replace_credentials=true to replace it.",
                            code=InvalidProviderCredentialContext.code,
                        )

                if replace:
                    # Explicit opt-in discards ALL old fields, including unknown
                    # auxiliary secrets. Never infer replacement from decrypt failure.
                    # Use the current field contract; c26 moved keyless and
                    # provider-configuration checks to serializer.validate().
                    validator = AgentccProviderCredentialCreateSerializer(
                        data={"provider_name": instance.provider_name}
                    )
                    try:
                        credentials = validator.fields["credentials"].run_validation(
                            credentials
                        )
                    except ValidationError:
                        return self._gm.bad_request(
                            "A complete valid credential set is required."
                        )
                else:
                    # Neither omitted nor blank auxiliary fields erase stored values.
                    credentials = {**stored, **_without_blank_credentials(credentials)}
                credential_context_strings(credentials)
                values = instance.models_list
                values = values if isinstance(values, (list, tuple)) else []
                projected = (
                    {"models": [], "dropped_model_count": len(values)}
                    if prior_unreadable
                    else filter_provider_model_ids(
                        values,
                        (
                            prior,
                            credentials,
                            provider_url_auth_values(instance.base_url),
                            extra_config_secret_values(instance.extra_config),
                        ),
                        provider_name=instance.provider_name,
                        api_format=instance.api_format,
                        enforce_discovery_grammar=False,
                    )
                )
                # Keep ciphertext and the sanitized list in the same row update,
                # under the org/row locks, before any publication or response.
                instance.models_list = projected["models"]
                instance.encrypted_credentials = CredentialManager.encrypt(credentials)
                instance.last_rotated_at = timezone.now()
                instance.save(
                    update_fields=[
                        "encrypted_credentials",
                        "models_list",
                        "last_rotated_at",
                        "updated_at",
                    ]
                )
                synced = self._push_config_to_gateway(instance.organization)

            data = _public_provider_data(instance, credentials, prior)
            if projected["dropped_model_count"]:
                data["dropped_model_count"] = projected["dropped_model_count"]
            if prior_unreadable:
                data["code"] = InvalidProviderCredentialContext.code
                data["model_warning"] = (
                    "Credential replaced; the prior credential could not be validated. "
                    "All saved model IDs were cleared. Re-enter trusted model IDs "
                    "before using this provider."
                )
            data["gateway_synced"] = synced
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except InvalidProviderCredentialContext:
            return self._gm.custom_error_response(
                400,
                str(InvalidProviderCredentialContext()),
                code=InvalidProviderCredentialContext.code,
            )
        except Exception as e:
            return exception_response(
                self._gm, e, logger=logger, event="provider_credential_rotate_error"
            )

    @action(detail=False, methods=["post"])
    def fetch_models(self, request):
        """Fetch available models from a provider's API.

        A supplied key never loads a saved row, even with provider_name. Saved
        credentials are consulted only for saved-key listing or saved-list
        projection; projection cannot be combined with a caller-supplied key.
        """
        if (
            request.data.get("saved_models_only") is not True
            and getattr(settings, "GATEWAY_PROVIDER_MODEL_DISCOVERY_ENABLED", False)
            is not True
        ):
            # Refuse before reading credentials, selecting a URL, or transport.
            # Saved-only hydration is a local projection, not discovery.
            return self._gm.custom_error_response(
                403,
                "Provider model discovery is disabled. Enter model IDs manually; "
                "saving and rotating credentials remain available.",
                code="provider_model_discovery_disabled",
            )
        provider_name = request.data.get("provider_name")
        request_key = request.data.get("api_key", "")
        try:
            if not isinstance(request_key, str):
                raise InvalidProviderCredentialContext
            credential_context_strings(request_key)
        except InvalidProviderCredentialContext:
            return self._gm.custom_error_response(
                400,
                str(InvalidProviderCredentialContext()),
                code=InvalidProviderCredentialContext.code,
            )
        saved_models_only = request.data.get("saved_models_only") is True
        if saved_models_only and request_key:
            return self._gm.bad_request(
                "Saved model projection cannot be combined with an api_key."
            )
        api_key = None
        base_url = None
        api_format = None
        api_path_prefix = None
        saved_base_url = None
        saved_models = []
        saved_secrets = []

        # This guard is a confidentiality boundary, not a fallback optimization:
        # caller-key responses must not depend on any saved row or decrypt result.
        if provider_name and not request_key:
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
                    return self._gm.custom_error_response(
                        400,
                        "Saved provider credential could not be decrypted. "
                        "Rotate the credential or check the encryption configuration.",
                        code=InvalidProviderCredentialContext.code,
                    )
                try:
                    if not isinstance(decrypted, dict):
                        raise InvalidProviderCredentialContext
                    saved_secrets = [
                        decrypted,
                        extra_config_secret_values(cred.extra_config),
                        provider_url_auth_values(cred.base_url),
                    ]
                    credential_context_strings(saved_secrets)
                except Exception:
                    if saved_models_only and _settings_are_text(decrypted, cred):
                        # The credential decrypts but holds a value that is not
                        # text. The edit dialog loads the projection the console
                        # read already shows (the held-back IDs counted, never
                        # named), so a complete new credential can replace it.
                        return self._gm.success_response(
                            public_provider_models(cred, credentials=decrypted)
                        )
                    # No decoder text, traceback or credential values in logs.
                    return self._gm.custom_error_response(
                        400,
                        str(InvalidProviderCredentialContext()),
                        code=InvalidProviderCredentialContext.code,
                    )
                api_key = decrypted.get("api_key", "")
                base_url = cred.base_url.rstrip("/") if cred.base_url else ""
                saved_base_url = base_url
                api_format = cred.api_format
                api_path_prefix = (cred.extra_config or {}).get("api_path_prefix")
                saved_models = (
                    list(cred.models_list)
                    if isinstance(cred.models_list, (list, tuple))
                    else []
                )

        # Hydration is a projection of the scoped saved row, never a provider
        # probe. It preserves manual IDs regardless of discovery grammar.
        if saved_models_only:
            if not provider_name:
                return self._gm.bad_request("Saved provider context is required.")
            projected = filter_provider_model_ids(
                saved_models,
                (request_key, saved_secrets),
                provider_name=provider_name,
                api_format=api_format or "openai",
                enforce_discovery_grammar=False,
            )
            return self._gm.success_response(projected)

        # The saved key only ever goes where it was saved for: a caller who can
        # use the credential but not read it must not be able to redirect it.
        uses_saved_key = saved_base_url is not None and not request_key
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
        base_url = (
            requested_base_url
            if request_key and "base_url" in request.data
            else (requested_base_url or base_url or "")
        ).rstrip("/")
        api_key = request_key or api_key or ""
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

        # A decision provider is never probed: a listing call is not free on
        # every decision vendor, and its models come from the preset or the
        # user. What was saved is the list.
        try:
            url_secrets = provider_url_auth_values(base_url)
            credential_context_strings(url_secrets)
            is_system_one = api_format in _SYSTEM_ONE_API_FORMATS
            models = (
                saved_models
                if is_system_one
                else self._fetch_models_from_provider(
                    provider_name, base_url, api_key, api_format, api_path_prefix
                )
            )
            projected = filter_provider_model_ids(
                models,
                (api_key, saved_secrets, url_secrets)
                if uses_saved_key
                else (api_key, url_secrets),
                provider_name=provider_name,
                api_format=api_format,
                normalize_google=not is_system_one,
                enforce_discovery_grammar=not uses_saved_key,
            )
            if not is_system_one:
                projected["models"].sort()
            # Preserve the existing no-drop response contract, including System One.
            if not projected["dropped_model_count"]:
                projected.pop("dropped_model_count")
            return self._gm.success_response(projected)
        except InvalidProviderCredentialContext:
            return self._gm.custom_error_response(
                400,
                str(InvalidProviderCredentialContext()),
                code=InvalidProviderCredentialContext.code,
            )
        except _ProviderRedirectRefused:
            return self._gm.custom_error_response(
                400, PROVIDER_REDIRECT_MESSAGE, code="PROVIDER_REDIRECT_REFUSED"
            )
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
        """Read raw IDs for fetch_models to project before returning to the client.

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
                allow_redirects=False,
            )
            _refuse_provider_redirect(resp)
            resp.raise_for_status()
            data = resp.json()
            return [
                m.get("id") if isinstance(m, dict) else None
                for m in data.get("data", [])
            ]

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
                allow_redirects=False,
            )
            _refuse_provider_redirect(resp)
            try:
                resp.raise_for_status()
            except HTTPError:
                # Only the Google protocol's structured credential refusal is
                # a key verdict. Unknown/malformed 400s keep their HTTPError.
                if _google_key_rejected(resp):
                    raise ProviderAuthenticationRejected from None
                raise
            data = resp.json()
            # Keep raw resource names until the credential-aware projection.
            # Stripping first could erase the portion matching a saved secret.
            return [
                m.get("name") if isinstance(m, dict) else None
                for m in data.get("models", [])
            ]

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
                allow_redirects=False,
            )
            _refuse_provider_redirect(resp)
            resp.raise_for_status()
            data = resp.json()
            # Cohere returns {"models": [{"name": "..."}]}; compat returns OpenAI shape.
            if "data" in data:
                return [
                    m.get("id") if isinstance(m, dict) else None for m in data["data"]
                ]
            return [
                m.get("name") if isinstance(m, dict) else None
                for m in data.get("models", [])
            ]

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
            allow_redirects=False,
        )
        _refuse_provider_redirect(resp)
        resp.raise_for_status()
        data = resp.json()
        return [
            m.get("id") if isinstance(m, dict) else None for m in data.get("data", [])
        ]

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
                    config = AgentccOrgConfig(
                        organization=org,
                        version=1,
                        is_active=True,
                        managed_models=initial_managed_models(org),
                    )
                    config.disable_auto_workspace_assignment = True
                    config.save(force_insert=True)
                return push_org_config(str(org.id), config)
        except Exception as e:
            logger.warning("config_push_failed", org_id=str(org.id), error=str(e))
            return False
