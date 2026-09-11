import requests as http_requests
import structlog
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from integrations.services.credentials import CredentialManager
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.serializers.provider_credential import (
    AgentccProviderCredentialCreateSerializer,
    AgentccProviderCredentialSerializer,
    AgentccProviderCredentialUpdateSerializer,
)
from agentcc.services.config_push import push_org_config
from agentcc.services.url_safety import build_ssrf_safe_session, ensure_public_http_url
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

_GATEWAY_SYNC_WARNING = (
    "Config saved but gateway sync failed. Changes will apply on next gateway restart."
)


class AgentccProviderCredentialViewSet(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = AgentccProviderCredentialSerializer
    queryset = AgentccProviderCredential.no_workspace_objects.all()
    _gm = GeneralMethods()

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
            logger.exception("provider_credential_list_error", error=str(e))
            return self._gm.bad_request(str(e))

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            return self._gm.success_response(
                AgentccProviderCredentialSerializer(instance).data
            )
        except Exception as e:
            logger.exception("provider_credential_retrieve_error", error=str(e))
            return self._gm.not_found("Provider credential not found")

    def create(self, request, *args, **kwargs):
        try:
            serializer = AgentccProviderCredentialCreateSerializer(data=request.data)
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
            logger.exception("provider_credential_create_error", error=str(e))
            return self._gm.bad_request(str(e))

    def partial_update(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = AgentccProviderCredentialUpdateSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

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
            logger.exception("provider_credential_update_error", error=str(e))
            return self._gm.bad_request(str(e))

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
            logger.exception("provider_credential_delete_error", error=str(e))
            return self._gm.bad_request(str(e))

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
            logger.exception("provider_credential_rotate_error", error=str(e))
            return self._gm.bad_request(str(e))

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
                api_format = cred.api_format

        # Raw values from request body override or fill gaps.
        base_url = (request.data.get("base_url") or base_url or "").rstrip("/")
        api_key = request.data.get("api_key") or api_key or ""
        api_format = request.data.get("api_format") or api_format or "openai"

        if not api_key:
            msg = (
                f"No saved credential found for provider '{provider_name}'. "
                "Provide an api_key to fetch models."
                if provider_name
                else "api_key is required"
            )
            return self._gm.bad_request(msg)

        try:
            models = self._fetch_models_from_provider(
                provider_name, base_url, api_key, api_format
            )
            return self._gm.success_response({"models": models})
        except ValueError as e:
            return self._gm.bad_request(str(e))
        except ConnectionError as e:
            return self._gm.bad_request(str(e))
        except Exception as e:
            logger.warning(
                "fetch_models_error",
                provider_name=provider_name,
                api_format=api_format,
                base_url=base_url,
                error=str(e),
            )
            # Never expose raw exception message — may contain credentials/URLs
            return self._gm.success_response(
                {"models": [], "error": "Failed to fetch models from provider"}
            )

    def _fetch_models_from_provider(self, provider_name, base_url, api_key, api_format):
        """Call the provider's list-models endpoint and return a sorted list of model IDs.

        Listing models is an identity lookup, not a translation call — so it uses
        the provider's *native* protocol regardless of ``api_format`` (which
        only describes how the gateway translates at runtime). Branches on
        ``provider_name`` first; falls back to ``api_format`` for custom /
        unknown providers.
        """
        timeout = 15

        # Validate user-supplied base_url and use safe session to prevent SSRF
        # (including DNS rebinding).
        if base_url:
            ensure_public_http_url(base_url, "Invalid base URL")
            http = build_ssrf_safe_session("Connection to private address blocked")
        else:
            http = http_requests

        name = (provider_name or "").lower()

        if name == "anthropic" or api_format == "anthropic":
            url = f"{base_url or 'https://api.anthropic.com'}/v1/models"
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

        if name in ("google", "gemini", "google_gemini") or api_format in ("gemini", "google"):
            url = "https://generativelanguage.googleapis.com/v1beta/models"
            resp = http.get(
                url,
                params={"key": api_key},
                timeout=timeout,
            )
            resp.raise_for_status()
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
            # Cohere native: /v1/models with Bearer auth.
            base = (base_url or "https://api.cohere.com").rstrip("/")
            if base.endswith("/compatibility/v1"):
                url = f"{base}/models"
            else:
                url = f"{base}/v1/models"
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
        # Azure OpenAI-compat, custom/self-hosted).
        url = f"{base_url or 'https://api.openai.com/v1'}/models"
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
            config = AgentccOrgConfig.no_workspace_objects.filter(
                organization=org, is_active=True, deleted=False
            ).first()
            if not config:
                config = AgentccOrgConfig.no_workspace_objects.create(
                    organization=org, version=1, is_active=True
                )
            return push_org_config(str(org.id), config)
        except Exception as e:
            logger.warning("config_push_failed", org_id=str(org.id), error=str(e))
            return False
