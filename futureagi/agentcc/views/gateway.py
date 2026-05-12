import datetime
import os
import uuid

import structlog
from django.core.cache import cache
from django.db import transaction
from django.db.models import Avg, Count, Max, Q
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ViewSet

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.models.request_log import AgentccRequestLog
from agentcc.org_config_defaults import (
    default_cost_tracking_config,
    normalize_cost_tracking_config,
)
from agentcc.services.config_push import push_all_org_configs, push_org_config
from agentcc.services.gateway_client import (
    AGENTCC_ADMIN_TOKEN,
    AGENTCC_GATEWAY_URL,
    GatewayClientError,
    get_gateway_client,
)
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

_GATEWAY_SYNC_WARNING = (
    "Config saved but gateway sync failed. Changes will apply on next gateway restart."
)

# Public-facing URL shown to users in the dashboard (e.g. https://gateway.futureagi.com).
# Falls back to AGENTCC_GATEWAY_URL if not set.
AGENTCC_GATEWAY_PUBLIC_URL = (
    os.environ.get("AGENTCC_GATEWAY_PUBLIC_URL", "") or AGENTCC_GATEWAY_URL
)


_BUDGET_LEVEL_KEY_ALIASES = {
    "orgLimit": "org_limit",
    "hardLimit": "hard_limit",
    "perKey": "per_key",
    "perUser": "per_user",
    "perModel": "per_model",
}


def _normalize_budget_level_key(level):
    if not isinstance(level, str):
        return level
    return _BUDGET_LEVEL_KEY_ALIASES.get(level, level)


def _set_budget_level_config(budgets, level, budget_config):
    if not isinstance(budgets, dict):
        budgets = {}
    else:
        budgets = {**budgets}

    normalized_level = _normalize_budget_level_key(level)
    if normalized_level != level:
        budgets.pop(level, None)
    budgets[normalized_level] = budget_config
    return budgets


def _remove_budget_level_config(budgets, level):
    if not isinstance(budgets, dict):
        return budgets

    updated = {**budgets}
    normalized_level = _normalize_budget_level_key(level)
    updated.pop(level, None)
    updated.pop(normalized_level, None)
    return updated


def _build_virtual_gateway(status="healthy", health=None):
    """Build the virtual gateway dict returned by list/retrieve."""
    data = {
        "id": "default",
        "name": "Agent Command Center Gateway",
        "base_url": AGENTCC_GATEWAY_PUBLIC_URL.rstrip("/") + "/v1",
        "status": status,
    }
    if health:
        # Don't let the gateway's raw status (e.g. "ok") overwrite ours
        health.pop("status", None)
        data.update(health)
    return data


class AgentccGatewayViewSet(ViewSet):
    """
    Stateless proxy to the Go gateway (configured via env vars).
    No DB model — returns a virtual singleton gateway with live health.
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def _current_org(self, request):
        return getattr(request, "organization", None)

    def _current_org_id(self, request):
        organization = self._current_org(request)
        return str(organization.id) if organization else ""

    # ------------------------------------------------------------------
    # list / retrieve — virtual gateway with live health
    # ------------------------------------------------------------------

    def list(self, request, *args, **kwargs):
        try:
            status = "healthy"
            health = {}
            try:
                client = get_gateway_client()
                health = client.health_check()
            except GatewayClientError:
                status = "unreachable"

            data = _build_virtual_gateway(status=status, health=health)

            org = self._current_org(request)
            if org:
                self._enrich_with_org_provider_counts(data, org)
            return self._gm.success_response([data])
        except Exception as e:
            logger.exception("gateway_list_error", error=str(e))
            return self._gm.bad_request(str(e))

    def retrieve(self, request, *args, **kwargs):
        """Accept any pk and ignore it — there is only one gateway."""
        try:
            status = "healthy"
            health = {}
            try:
                client = get_gateway_client()
                health = client.health_check()
            except GatewayClientError:
                status = "unreachable"

            data = _build_virtual_gateway(status=status, health=health)

            org = self._current_org(request)
            if org:
                self._enrich_with_org_provider_counts(data, org)
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("gateway_retrieve_error", error=str(e))
            return self._gm.not_found("Gateway not found")

    def _enrich_with_org_provider_counts(self, data, org):
        providers = AgentccProviderCredential.no_workspace_objects.filter(
            organization=org,
            is_active=True,
            deleted=False,
        )
        provider_count = providers.count()
        model_count = sum(len(p.models_list or []) for p in providers)
        if isinstance(data, list):
            for item in data:
                item["provider_count"] = provider_count
                item["model_count"] = model_count
        else:
            data["provider_count"] = provider_count
            data["model_count"] = model_count
        return data

    # ------------------------------------------------------------------
    # health_check
    # ------------------------------------------------------------------

    @action(detail=True, methods=["post"])
    def health_check(self, request, pk=None):
        try:
            client = get_gateway_client()
            org = self._current_org(request)

            try:
                health = client.health_check()

                db_providers = AgentccProviderCredential.no_workspace_objects.filter(
                    organization=org, is_active=True, deleted=False
                )
                provider_health = {
                    "providers": [
                        {
                            "name": p.provider_name,
                            "display_name": p.display_name or p.provider_name,
                            "models": p.models_list or [],
                            "status": "configured",
                        }
                        for p in db_providers
                    ]
                }

                providers = provider_health.get("providers", [])
                p_count = len(providers)
                m_count = sum(
                    len(p.get("models", [])) for p in providers if isinstance(p, dict)
                )

                # Auto-sync org configs to gateway on first successful health check
                # (e.g. after gateway container restart). Throttled to once per 5 min.
                cache_key = "agentcc:gateway_config_synced"
                if not cache.get(cache_key):
                    try:
                        push_all_org_configs()
                        cache.set(cache_key, True, 300)
                        logger.info("gateway_auto_sync", reason="first_health_check")
                    except Exception:
                        logger.warning("gateway_auto_sync_failed", exc_info=True)

                data = {
                    "status": "healthy",
                    "health": health,
                    "providers": provider_health,
                    "provider_count": p_count,
                    "model_count": m_count,
                }
                return self._gm.success_response(data)
            except GatewayClientError as e:
                return self._gm.bad_request(
                    {
                        "status": "unreachable",
                        "error": str(e),
                    }
                )
        except Exception as e:
            logger.exception("health_check_error", error=str(e))
            return self._gm.bad_request(str(e))

    # ------------------------------------------------------------------
    # config / reload / update-config
    # ------------------------------------------------------------------

    @action(detail=True, methods=["get"])
    def config(self, request, pk=None):
        try:
            org = self._current_org(request)

            org_config = AgentccOrgConfig.no_workspace_objects.filter(
                organization=org, is_active=True, deleted=False
            ).first()
            org_data = {"cost_tracking": default_cost_tracking_config()}
            if org_config:
                from agentcc.serializers.org_config import AgentccOrgConfigSerializer

                org_data = AgentccOrgConfigSerializer(org_config).data

            providers = list(
                AgentccProviderCredential.no_workspace_objects.filter(
                    organization=org,
                    deleted=False,
                    is_active=True,
                ).values(
                    "id",
                    "provider_name",
                    "display_name",
                    "base_url",
                    "api_format",
                    "models_list",
                    "is_active",
                    "default_timeout_seconds",
                    "max_concurrent",
                    "conn_pool_size",
                    "created_at",
                )
            )
            providers_map = {}
            for p in providers:
                providers_map[p["provider_name"]] = {
                    "id": str(p["id"]),
                    "name": p["provider_name"],
                    "display_name": p["display_name"] or p["provider_name"],
                    "base_url": p["base_url"],
                    "api_format": p["api_format"],
                    "models": p["models_list"] or [],
                    "is_active": p["is_active"],
                    "default_timeout": p["default_timeout_seconds"],
                    "max_concurrent": p["max_concurrent"],
                    "conn_pool_size": p["conn_pool_size"],
                }

            gateway_status = "unreachable"
            try:
                client = get_gateway_client()
                client.health_check()
                gateway_status = "healthy"
            except GatewayClientError:
                pass

            result = {**org_data}
            result["providers"] = providers_map
            result["gateway"] = {"status": gateway_status}
            return self._gm.success_response(result)
        except Exception as e:
            logger.exception("config_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["post"])
    def reload(self, request, pk=None):
        """Re-push this org's config to the gateway."""
        try:
            org = self._current_org(request)
            synced = self._push_current_config(org)
            data = {"status": "ok", "gateway_synced": synced}
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("reload_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["post"], url_path="update-config")
    def update_config(self, request, pk=None):
        """Patch one or more JSON fields on the org's active config and push."""
        try:
            org = self._current_org(request)
            config_patch = request.data
            if not config_patch or not isinstance(config_patch, dict):
                return self._gm.bad_request("Config patch must be a JSON object")

            allowed_fields = {
                "guardrails",
                "routing",
                "cache",
                "rate_limiting",
                "budgets",
                "cost_tracking",
                "ip_acl",
                "alerting",
                "privacy",
                "tool_policy",
                "mcp",
                "a2a",
                "audit",
                "model_database",
                "model_map",
            }
            invalid = set(config_patch.keys()) - allowed_fields
            if invalid:
                return self._gm.bad_request(
                    f"Unknown config fields: {', '.join(sorted(invalid))}"
                )

            def _deep_merge(base, patch):
                """Recursively merge patch into base. None values = delete."""
                result = {**base}
                for k, v in patch.items():
                    if v is None:
                        result.pop(k, None)
                    elif isinstance(result.get(k), dict) and isinstance(v, dict):
                        result[k] = _deep_merge(result[k], v)
                    else:
                        result[k] = v
                return result

            def updater(cfg):
                for field, value in config_patch.items():
                    existing = getattr(cfg, field, None)
                    if isinstance(existing, dict) and isinstance(value, dict):
                        setattr(cfg, field, _deep_merge(existing, value))
                    else:
                        setattr(cfg, field, value)

            new_config, synced = self._update_org_config(
                org,
                updater,
                user=request.user,
                desc="Config update",
            )
            data = {"version": new_config.version, "gateway_synced": synced}
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("update_config_error", error=str(e))
            return self._gm.bad_request(str(e))

    # ------------------------------------------------------------------
    # update-provider / remove-provider
    # ------------------------------------------------------------------

    @action(detail=True, methods=["post"], url_path="update-provider")
    def update_provider(self, request, pk=None):
        """Add or update a provider credential for the org, then push config."""
        try:
            org = self._current_org(request)
            provider_name = request.data.get("name")
            provider_config = request.data.get("config")
            if not provider_name or not provider_config:
                return self._gm.bad_request("name and config are required")

            from integrations.services.credentials import CredentialManager

            api_key = provider_config.pop("api_key", None)
            credential_fields = (
                "api_secret",
                "access_key",
                "secret_key",
                "region",
                "aws_access_key_id",
                "aws_secret_access_key",
                "aws_region",
                "aws_session_token",
            )
            new_cred_values = {
                k: provider_config.pop(k)
                for k in list(provider_config)
                if k in credential_fields
            }
            if api_key is not None:
                new_cred_values["api_key"] = api_key

            defaults = {
                "base_url": provider_config.get("base_url", ""),
                "api_format": provider_config.get("api_format", "openai"),
                "models_list": provider_config.get("models", []),
                "default_timeout_seconds": provider_config.get(
                    "default_timeout_seconds",
                    provider_config.get("default_timeout", 60),
                ),
                "max_concurrent": provider_config.get("max_concurrent", 100),
                "conn_pool_size": provider_config.get("conn_pool_size", 100),
                "extra_config": {
                    k: v
                    for k, v in provider_config.items()
                    if k
                    not in (
                        "api_key",
                        "api_secret",
                        "access_key",
                        "secret_key",
                        "region",
                        "aws_access_key_id",
                        "aws_secret_access_key",
                        "aws_region",
                        "aws_session_token",
                        "base_url",
                        "api_format",
                        "models",
                        "default_timeout",
                        "default_timeout_seconds",
                        "max_concurrent",
                        "conn_pool_size",
                    )
                },
                "is_active": True,
                "display_name": provider_config.get("display_name", "")
                or provider_name,
            }
            lookup = dict(
                organization=org,
                provider_name=provider_name,
                deleted=False,
            )
            with transaction.atomic():
                try:
                    cred = AgentccProviderCredential.no_workspace_objects.select_for_update().get(
                        **lookup
                    )
                    for k, v in defaults.items():
                        setattr(cred, k, v)
                    # Merge new credential values into existing encrypted credentials.
                    if new_cred_values:
                        existing = CredentialManager.decrypt(cred.encrypted_credentials)
                        existing.update(new_cred_values)
                        cred.encrypted_credentials = CredentialManager.encrypt(existing)
                    cred.save()
                except AgentccProviderCredential.DoesNotExist:
                    if not new_cred_values:
                        return self._gm.bad_request(
                            "credentials are required for new providers"
                        )
                    defaults["encrypted_credentials"] = CredentialManager.encrypt(
                        new_cred_values
                    )
                    cred = AgentccProviderCredential.no_workspace_objects.create(
                        **lookup, **defaults
                    )

            synced = self._push_current_config(org)

            data = {
                "provider": provider_name,
                "action": "updated",
                "gateway_synced": synced,
            }
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("update_provider_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["post"], url_path="remove-provider")
    def remove_provider(self, request, pk=None):
        """Soft-delete a provider credential and push config."""
        try:
            org = self._current_org(request)
            provider_name = request.data.get("name")
            if not provider_name:
                return self._gm.bad_request("name is required")

            deleted_count = AgentccProviderCredential.no_workspace_objects.filter(
                organization=org,
                provider_name=provider_name,
                deleted=False,
            ).update(deleted=True, deleted_at=timezone.now())

            if not deleted_count:
                return self._gm.not_found(f"Provider '{provider_name}' not found")

            synced = self._push_current_config(org)

            data = {
                "provider": provider_name,
                "action": "removed",
                "gateway_synced": synced,
            }
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("remove_provider_error", error=str(e))
            return self._gm.bad_request(str(e))

    # ------------------------------------------------------------------
    # guardrails
    # ------------------------------------------------------------------

    @action(detail=True, methods=["post"], url_path="toggle-guardrail")
    def toggle_guardrail(self, request, pk=None):
        try:
            org = self._current_org(request)
            guardrail_name = request.data.get("name")
            enabled = request.data.get("enabled")
            if not guardrail_name or enabled is None:
                return self._gm.bad_request("name and enabled are required")

            def updater(cfg):
                guardrails = cfg.guardrails or {}
                rules = guardrails.get("rules", [])

                # Find existing rule or create one with defaults
                found = False
                for rule in rules:
                    if rule.get("name") == guardrail_name:
                        rule["enabled"] = enabled
                        found = True
                        break

                if not found:
                    # Upsert: create the rule with sensible defaults
                    rules.append(
                        {
                            "name": guardrail_name,
                            "enabled": enabled,
                            "action": "block",
                            "threshold": 0.8,
                            "stage": "pre",
                            "mode": "sync",
                            "config": {},
                        }
                    )

                # Also update checks dict if it exists
                checks = guardrails.get("checks", {})
                if guardrail_name in checks:
                    checks[guardrail_name]["enabled"] = enabled

                guardrails["rules"] = rules
                guardrails["checks"] = checks
                cfg.guardrails = guardrails

            new_config, synced = self._update_org_config(
                org,
                updater,
                user=request.user,
                desc=f"Toggle guardrail {guardrail_name} {'on' if enabled else 'off'}",
            )
            data = {
                "guardrail": guardrail_name,
                "enabled": enabled,
                "version": new_config.version,
                "gateway_synced": synced,
            }
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("toggle_guardrail_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["get"], url_path="protect-templates")
    def protect_templates(self, request):
        """Return eval templates compatible with the FI protect guardrail."""
        try:
            from model_hub.models.evals_metric import EvalTemplate

            # Protect-compatible metric names (from DeterministicEvaluator._resolve_protect_metric)
            protect_metrics = [
                "toxicity",
                "bias_detection",
                "prompt_injection",
                "data_privacy_compliance",
                "pii",
                "content_moderation",
            ]

            templates = EvalTemplate.objects.filter(
                owner="system",
                deleted=False,
                config__eval_type_id="DeterministicEvaluator",
                name__in=protect_metrics,
            ).values("eval_id", "name", "description")

            result = [
                {
                    "eval_id": t["eval_id"],
                    "name": t["name"],
                    "description": t["description"] or "",
                }
                for t in templates
            ]

            return self._gm.success_response(result)
        except Exception as e:
            logger.exception("protect_templates_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["post"], url_path="update-guardrail")
    def update_guardrail(self, request, pk=None):
        try:
            org = self._current_org(request)
            guardrail_name = request.data.get("name")
            guardrail_config = request.data.get("config")
            if not guardrail_name or not guardrail_config:
                return self._gm.bad_request("name and config are required")

            def updater(cfg):
                guardrails = cfg.guardrails or {}
                rules = guardrails.get("rules", [])
                found = False
                for i, rule in enumerate(rules):
                    if rule.get("name") == guardrail_name:
                        rules[i] = {**guardrail_config, "name": guardrail_name}
                        found = True
                        break
                if not found:
                    rules.append({**guardrail_config, "name": guardrail_name})
                guardrails["rules"] = rules
                cfg.guardrails = guardrails

            new_config, synced = self._update_org_config(
                org,
                updater,
                user=request.user,
                desc=f"Update guardrail {guardrail_name}",
            )
            data = {
                "guardrail": guardrail_name,
                "action": "updated",
                "version": new_config.version,
                "gateway_synced": synced,
            }
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("update_guardrail_error", error=str(e))
            return self._gm.bad_request(str(e))

    # ------------------------------------------------------------------
    # test-playground
    # ------------------------------------------------------------------

    @action(detail=True, methods=["post"], url_path="test-playground")
    def test_playground(self, request, pk=None):
        """Send a real chat completion through the gateway to test guardrails."""
        playground_key_id = None
        try:
            client = get_gateway_client()

            prompt = request.data.get("prompt", "").strip()
            model = request.data.get("model", "")
            system_prompt = request.data.get("system_prompt", "")
            if not prompt:
                return self._gm.bad_request("prompt is required")

            # Ensure the org's active config is pushed to the gateway
            org_id = self._current_org_id(request)
            if not org_id:
                return self._gm.bad_request(
                    "An active organization is required for playground testing"
                )
            if not self._ensure_org_config_pushed(client, org_id):
                return self._gm.bad_request(
                    "Could not sync the active organization config to the gateway"
                )

            playground_key_id, api_key = self._create_playground_api_key(client, org_id)
            if not api_key:
                return self._gm.bad_request(
                    "Could not obtain an API key for playground testing"
                )

            # If no model specified, try to pick the first available one
            if not model:
                model = self._pick_default_model(client, org_id=org_id)
                if not model:
                    return self._gm.bad_request(
                        "No model specified and no providers configured. "
                        "Please specify a model or configure a provider first."
                    )

            status_code, body, headers = client.send_chat_completion(
                prompt=prompt,
                model=model,
                api_key=api_key,
                system_prompt=system_prompt or None,
                cache_control="no-store",
            )

            # Extract guardrail info from response
            guardrail_info = {}
            for key, val in headers.items():
                lk = key.lower()
                if "guardrail" in lk or "x-agentcc" in lk:
                    guardrail_info[key] = val

            return self._gm.success_response(
                {
                    "status_code": status_code,
                    "body": body,
                    "guardrail_headers": guardrail_info,
                    "model": model,
                    "blocked": status_code in (403, 446),
                    "warned": status_code == 246,
                }
            )
        except GatewayClientError as e:
            return self._gm.bad_request(f"Gateway error: {e}")
        except Exception as e:
            logger.exception("test_playground_error", error=str(e))
            return self._gm.bad_request(str(e))
        finally:
            if playground_key_id:
                try:
                    client.revoke_key(playground_key_id)
                except GatewayClientError as e:
                    logger.warning(
                        "playground_key_revoke_failed",
                        key_id=playground_key_id,
                        error=str(e),
                    )

    def _ensure_org_config_pushed(self, client, org_id):
        """Push the org's active config to the gateway (idempotent)."""
        try:
            config = AgentccOrgConfig.no_workspace_objects.filter(
                organization_id=org_id, is_active=True, deleted=False
            ).first()
            if not config:
                return True

            if not push_org_config(org_id, config):
                logger.warning("playground_config_push_failed", org_id=org_id)
                return False

            client.get_org_config(org_id)
            return True
        except Exception as e:
            logger.warning("playground_config_push_failed", org_id=org_id, error=str(e))
            return False

    def _create_playground_api_key(self, client, org_id):
        playground_key_name = f"__playground_{org_id[:8]}_{uuid.uuid4().hex[:8]}__"
        try:
            result = client.create_key(
                name=playground_key_name,
                owner="playground",
                metadata={
                    "org_id": str(org_id),
                    "purpose": "playground-testing",
                    "allow_policy_override": "true",
                },
            )
            return result.get("id", ""), result.get("key", "")
        except GatewayClientError as e:
            logger.warning("playground_key_create_failed", org_id=org_id, error=str(e))
            return None, None

    def _pick_default_model(self, client, org_id=None):
        if not org_id:
            return None
        try:
            cred = AgentccProviderCredential.no_workspace_objects.filter(
                organization_id=org_id,
                is_active=True,
                deleted=False,
            ).first()
            if cred and cred.models_list:
                first = cred.models_list[0]
                return first if isinstance(first, str) else first.get("name", "")
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # budgets
    # ------------------------------------------------------------------

    @action(detail=True, methods=["post"], url_path="set-budget")
    def set_budget(self, request, pk=None):
        try:
            org = self._current_org(request)
            level = request.data.get("level")
            budget_config = request.data.get("config")
            if not level or not budget_config:
                return self._gm.bad_request("level and config are required")

            def updater(cfg):
                cfg.budgets = _set_budget_level_config(
                    cfg.budgets,
                    level,
                    budget_config,
                )

            new_config, synced = self._update_org_config(
                org,
                updater,
                user=request.user,
                desc=f"Set budget {level}",
            )
            data = {
                "budget": level,
                "action": "set",
                "version": new_config.version,
                "gateway_synced": synced,
            }
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("set_budget_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["post"], url_path="remove-budget")
    def remove_budget(self, request, pk=None):
        try:
            org = self._current_org(request)
            level = request.data.get("level")
            if not level:
                return self._gm.bad_request("level is required")

            def updater(cfg):
                cfg.budgets = _remove_budget_level_config(cfg.budgets, level)

            new_config, synced = self._update_org_config(
                org,
                updater,
                user=request.user,
                desc=f"Remove budget {level}",
            )
            data = {
                "budget": level,
                "action": "removed",
                "version": new_config.version,
                "gateway_synced": synced,
            }
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("remove_budget_error", error=str(e))
            return self._gm.bad_request(str(e))

    # --- Batch API proxy ---

    @action(detail=True, methods=["post"], url_path="submit-batch")
    def submit_batch(self, request, pk=None):
        try:
            client = get_gateway_client()
            requests_list = request.data.get("requests", [])
            max_concurrency = request.data.get("max_concurrency", 5)
            if not requests_list:
                return self._gm.bad_request("requests array is required")
            result = client.submit_batch(requests_list, max_concurrency)
            batch_id = result.get("batch_id") or result.get("id")
            if batch_id:
                org_id = self._current_org_id(request)
                cache.set(f"agentcc_batch:{batch_id}", org_id, timeout=86400)
            return self._gm.success_response(result)
        except GatewayClientError as e:
            return self._gm.bad_request(f"Gateway error: {e}")
        except Exception as e:
            logger.exception("submit_batch_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["get"], url_path="get-batch")
    def get_batch(self, request, pk=None):
        try:
            client = get_gateway_client()
            batch_id = request.query_params.get("batch_id")
            if not batch_id:
                return self._gm.bad_request("batch_id query param is required")
            owner = cache.get(f"agentcc_batch:{batch_id}")
            if owner is None or owner != self._current_org_id(request):
                return self._gm.not_found("Batch not found")
            result = client.get_batch(batch_id)
            return self._gm.success_response(result)
        except GatewayClientError as e:
            return self._gm.bad_request(f"Gateway error: {e}")
        except Exception as e:
            logger.exception("get_batch_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["post"], url_path="cancel-batch")
    def cancel_batch(self, request, pk=None):
        try:
            client = get_gateway_client()
            batch_id = request.data.get("batch_id")
            if not batch_id:
                return self._gm.bad_request("batch_id is required")
            owner = cache.get(f"agentcc_batch:{batch_id}")
            if owner is None or owner != self._current_org_id(request):
                return self._gm.not_found("Batch not found")
            result = client.cancel_batch(batch_id)
            return self._gm.success_response(result)
        except GatewayClientError as e:
            return self._gm.bad_request(f"Gateway error: {e}")
        except Exception as e:
            logger.exception("cancel_batch_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["get"])
    def providers(self, request, pk=None):
        try:
            client = get_gateway_client()

            org = getattr(request, "organization", None)
            db_credentials = {}
            if org:
                for cred in AgentccProviderCredential.no_workspace_objects.filter(
                    organization=org, is_active=True, deleted=False
                ):
                    db_credentials[cred.provider_name] = {
                        "id": str(cred.id),
                        "display_name": cred.display_name,
                        "base_url": cred.base_url,
                        "api_format": cred.api_format,
                        "models": cred.models_list,
                    }

            gateway_health = {}
            try:
                provider_health = client.provider_health()
                gw_providers = provider_health.get("providers", [])
                if isinstance(gw_providers, list):
                    for p in gw_providers:
                        pid = p.get("id") or p.get("name") or p.get("provider_name")
                        if pid:
                            gateway_health[pid] = p
                elif isinstance(gw_providers, dict):
                    gateway_health = gw_providers
            except Exception:
                pass

            # Per-org provider stats from request logs (last 24h)
            provider_stats = {}
            if org:
                since = timezone.now() - datetime.timedelta(hours=24)
                stats_qs = (
                    AgentccRequestLog.no_workspace_objects.filter(
                        organization=org,
                        started_at__gte=since,
                    )
                    .values("provider")
                    .annotate(
                        request_count=Count("id"),
                        avg_latency_ms=Avg("latency_ms"),
                        error_count=Count("id", filter=Q(is_error=True)),
                    )
                )
                for row in stats_qs:
                    pname = row["provider"]
                    req_count = row["request_count"]
                    provider_stats[pname] = {
                        "request_count": req_count,
                        "avg_latency_ms": round(row["avg_latency_ms"] or 0, 2),
                        "error_rate": round(
                            (
                                (row["error_count"] / req_count * 100)
                                if req_count > 0
                                else 0.0
                            ),
                            2,
                        ),
                    }

            merged = []
            for pid, creds in db_credentials.items():
                gw_data = gateway_health.get(pid, {})
                pstats = provider_stats.get(pid, {})
                healthy = gw_data.get("healthy", True)
                circuit_state = (gw_data.get("circuit_state") or "n/a").lower()
                if not healthy or circuit_state == "open":
                    status = "unhealthy"
                elif circuit_state == "half_open":
                    status = "degraded"
                else:
                    status = "healthy"
                merged.append(
                    {
                        "id": pid,
                        "name": pid,
                        "status": status,
                        "healthy": healthy,
                        "circuit_state": circuit_state,
                        "display_name": creds.get("display_name", ""),
                        "base_url": creds.get("base_url", ""),
                        "api_format": creds.get("api_format", ""),
                        "models": creds.get("models", []),
                        "request_count": pstats.get("request_count", 0),
                        "avg_latency": pstats.get("avg_latency_ms", 0),
                        "error_rate": pstats.get("error_rate", 0),
                    }
                )

            return self._gm.success_response({"providers": merged})
        except GatewayClientError as e:
            logger.exception("providers_gateway_error", error=str(e))
            return self._gm.bad_request(str(e))
        except Exception as e:
            logger.exception("providers_error", error=str(e))
            return self._gm.bad_request(str(e))

    # --- MCP ---

    @action(detail=True, methods=["get"], url_path="mcp-status")
    def mcp_status(self, request, pk=None):
        try:
            org = self._current_org(request)
            org_config = AgentccOrgConfig.no_workspace_objects.filter(
                organization=org, is_active=True, deleted=False
            ).first()
            mcp_cfg = (org_config.mcp or {}) if org_config else {}
            servers = mcp_cfg.get("servers", {})

            if not servers:
                return self._gm.success_response(
                    {
                        "enabled": False,
                        "sessions": 0,
                        "tools": 0,
                        "resources": 0,
                        "prompts": 0,
                        "servers": [],
                    }
                )

            try:
                client = get_gateway_client()
                result = client.mcp_status()
                gw_servers = result.get("servers", [])
                if isinstance(gw_servers, list):
                    result["servers"] = [
                        s
                        for s in gw_servers
                        if isinstance(s, dict) and s.get("id", s.get("name")) in servers
                    ]
                return self._gm.success_response(result)
            except (GatewayClientError, Exception) as e:
                logger.debug("mcp_status unavailable: %s", e)
                return self._gm.success_response(
                    {
                        "enabled": True,
                        "sessions": 0,
                        "tools": 0,
                        "resources": 0,
                        "prompts": 0,
                        "servers": list(servers.keys()),
                    }
                )
        except Exception as e:
            logger.exception("mcp_status_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["get"], url_path="mcp-tools")
    def mcp_tools(self, request, pk=None):
        try:
            org = self._current_org(request)
            org_config = AgentccOrgConfig.no_workspace_objects.filter(
                organization=org, is_active=True, deleted=False
            ).first()
            mcp_cfg = (org_config.mcp or {}) if org_config else {}
            org_servers = set(mcp_cfg.get("servers", {}).keys())

            if not org_servers:
                return self._gm.success_response([])

            client = get_gateway_client()
            result = client.mcp_tools()
            if isinstance(result, list):
                result = [
                    t
                    for t in result
                    if isinstance(t, dict)
                    and t.get("server", t.get("server_id")) in org_servers
                ]
            return self._gm.success_response(result)
        except (GatewayClientError, Exception) as e:
            logger.debug("mcp_tools unavailable: %s", e)
            return self._gm.success_response([])

    @action(detail=True, methods=["post"], url_path="update-mcp-server")
    def update_mcp_server(self, request, pk=None):
        try:
            org = self._current_org(request)
            server_id = request.data.get("server_id")
            server_config = request.data.get("config")
            if not server_id or not server_config:
                return self._gm.bad_request("server_id and config are required")

            def updater(cfg):
                mcp_cfg = cfg.mcp or {}
                servers = mcp_cfg.get("servers", {})
                servers[server_id] = server_config
                mcp_cfg["servers"] = servers
                cfg.mcp = mcp_cfg

            new_config, synced = self._update_org_config(
                org,
                updater,
                user=request.user,
                desc=f"Update MCP server {server_id}",
            )
            data = {
                "server": server_id,
                "action": "updated",
                "version": new_config.version,
                "gateway_synced": synced,
            }
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("update_mcp_server_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["post"], url_path="remove-mcp-server")
    def remove_mcp_server(self, request, pk=None):
        try:
            org = self._current_org(request)
            server_id = request.data.get("server_id")
            if not server_id:
                return self._gm.bad_request("server_id is required")

            def updater(cfg):
                mcp_cfg = cfg.mcp or {}
                servers = mcp_cfg.get("servers", {})
                if server_id not in servers:
                    raise ValueError(f"MCP server '{server_id}' not found")
                del servers[server_id]
                mcp_cfg["servers"] = servers
                cfg.mcp = mcp_cfg

            try:
                new_config, synced = self._update_org_config(
                    org,
                    updater,
                    user=request.user,
                    desc=f"Remove MCP server {server_id}",
                )
            except ValueError as ve:
                return self._gm.not_found(str(ve))
            data = {
                "server": server_id,
                "action": "removed",
                "version": new_config.version,
                "gateway_synced": synced,
            }
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("remove_mcp_server_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["post"], url_path="update-mcp-guardrails")
    def update_mcp_guardrails(self, request, pk=None):
        try:
            org = self._current_org(request)
            guardrail_config = request.data.get("config")
            if not guardrail_config:
                return self._gm.bad_request("config is required")

            def updater(cfg):
                mcp_cfg = cfg.mcp or {}
                mcp_cfg["guardrails"] = guardrail_config
                cfg.mcp = mcp_cfg

            new_config, synced = self._update_org_config(
                org,
                updater,
                user=request.user,
                desc="Update MCP guardrails",
            )
            data = {
                "action": "updated",
                "version": new_config.version,
                "gateway_synced": synced,
            }
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("update_mcp_guardrails_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["post"], url_path="test-mcp-tool")
    def test_mcp_tool(self, request, pk=None):
        try:
            org = self._current_org(request)
            org_config = AgentccOrgConfig.no_workspace_objects.filter(
                organization=org, is_active=True, deleted=False
            ).first()
            mcp_cfg = (org_config.mcp or {}) if org_config else {}
            if not mcp_cfg.get("servers"):
                return self._gm.bad_request("No MCP servers configured for this org")

            client = get_gateway_client()
            name = request.data.get("name")
            arguments = request.data.get("arguments", {})
            if not name:
                return self._gm.bad_request("tool name is required")
            result = client.mcp_test_tool(name, arguments)
            return self._gm.success_response(result)
        except GatewayClientError as e:
            return self._gm.bad_request(f"Gateway error: {e}")
        except Exception as e:
            logger.exception("test_mcp_tool_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["get"], url_path="mcp-resources")
    def mcp_resources(self, request, pk=None):
        try:
            org = self._current_org(request)
            org_config = AgentccOrgConfig.no_workspace_objects.filter(
                organization=org, is_active=True, deleted=False
            ).first()
            mcp_cfg = (org_config.mcp or {}) if org_config else {}
            org_servers = set(mcp_cfg.get("servers", {}).keys())

            if not org_servers:
                return self._gm.success_response([])

            client = get_gateway_client()
            result = client.mcp_resources()
            if isinstance(result, list):
                result = [
                    r
                    for r in result
                    if isinstance(r, dict)
                    and r.get("server", r.get("server_id")) in org_servers
                ]
            return self._gm.success_response(result)
        except (GatewayClientError, Exception) as e:
            logger.debug("mcp_resources unavailable: %s", e)
            return self._gm.success_response([])

    @action(detail=True, methods=["get"], url_path="mcp-prompts")
    def mcp_prompts(self, request, pk=None):
        try:
            org = self._current_org(request)
            org_config = AgentccOrgConfig.no_workspace_objects.filter(
                organization=org, is_active=True, deleted=False
            ).first()
            mcp_cfg = (org_config.mcp or {}) if org_config else {}
            org_servers = set(mcp_cfg.get("servers", {}).keys())

            if not org_servers:
                return self._gm.success_response([])

            client = get_gateway_client()
            result = client.mcp_prompts()
            if isinstance(result, list):
                result = [
                    p
                    for p in result
                    if isinstance(p, dict)
                    and p.get("server", p.get("server_id")) in org_servers
                ]
            return self._gm.success_response(result)
        except (GatewayClientError, Exception) as e:
            logger.debug("mcp_prompts unavailable: %s", e)
            return self._gm.success_response([])

    # --- Per-org config helpers ---

    def _get_or_create_org_config(self, org):
        with transaction.atomic():
            config = (
                AgentccOrgConfig.no_workspace_objects.select_for_update()
                .filter(
                    organization=org,
                    is_active=True,
                    deleted=False,
                )
                .first()
            )
            if config:
                normalized = normalize_cost_tracking_config(config.cost_tracking)
                if normalized != config.cost_tracking:
                    config.cost_tracking = normalized
                    config.save(update_fields=["cost_tracking", "updated_at"])
                return config
            config, _ = AgentccOrgConfig.no_workspace_objects.get_or_create(
                organization=org,
                version=1,
                is_active=True,
                deleted=False,
                defaults={"cost_tracking": default_cost_tracking_config()},
            )
            normalized = normalize_cost_tracking_config(config.cost_tracking)
            if normalized != config.cost_tracking:
                config.cost_tracking = normalized
                config.save(update_fields=["cost_tracking", "updated_at"])
            return config

    def _update_org_config(self, org, updater_fn, user=None, desc=""):
        with transaction.atomic():
            active_config = (
                AgentccOrgConfig.no_workspace_objects.select_for_update()
                .filter(
                    organization=org,
                    is_active=True,
                    deleted=False,
                )
                .first()
            )
            if not active_config:
                active_config = AgentccOrgConfig.no_workspace_objects.create(
                    organization=org,
                    version=1,
                    is_active=True,
                    cost_tracking=default_cost_tracking_config(),
                )

            # Derive next version from locked active config — safe because
            # select_for_update prevents concurrent reads of this row.
            next_version = active_config.version + 1

            AgentccOrgConfig.no_workspace_objects.filter(
                organization=org,
                is_active=True,
                deleted=False,
            ).update(is_active=False)

            new_config = AgentccOrgConfig(
                organization=org,
                version=next_version,
                guardrails=active_config.guardrails,
                routing=active_config.routing,
                cache=active_config.cache,
                rate_limiting=active_config.rate_limiting,
                budgets=active_config.budgets,
                cost_tracking=normalize_cost_tracking_config(
                    active_config.cost_tracking
                ),
                ip_acl=active_config.ip_acl,
                alerting=active_config.alerting,
                privacy=active_config.privacy,
                tool_policy=active_config.tool_policy,
                mcp=active_config.mcp,
                a2a=active_config.a2a,
                audit=active_config.audit,
                model_database=active_config.model_database,
                model_map=active_config.model_map,
                is_active=True,
                created_by=user,
                change_description=desc or f"Config update v{next_version}",
            )
            updater_fn(new_config)
            new_config.save()

        synced = self._push_current_config(org, new_config)
        return new_config, synced

    def _push_current_config(self, org, config=None):
        """Returns True if gateway was updated, False if push failed."""
        try:
            if config is None:
                config = self._get_or_create_org_config(org)
            return push_org_config(str(org.id), config)
        except Exception as e:
            logger.warning("config_push_failed", org_id=str(org.id), error=str(e))
            return False
