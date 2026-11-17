"""The Console's model list: every model an organization can run, and who pays.

One row per model the Console's run path can serve for the org, in the
gateway's own payer words, in the run path's order (agentcc-gateway
internal/server/handlers.go orgRouteFor):

- ``byok``: one of the org's own usable providers serves it. That is a model
  map alias whose provider is usable, a "<provider>/<model>" id whose
  provider lists the model, or any model a usable provider lists (the lowest
  provider id wins). "Usable" is the gateway's rule: enabled and holding
  credentials. The providers are the ones FA pushes (config_push).
- ``managed``: no own key serves it, the org's Future AGI credits are on, and
  the managed catalog prices it.

``billing_mode`` is the payer the run path would pick today; the answer that
ran carries the gateway's ``x-agentcc-billing-mode``. Models only the gateway's
own platform providers serve are never listed. No credential, key, base URL or
credential id is returned.

Contract: lane-status gwc2197-CONSOLE-MODELS-CONTRACT-1114.md (r2).
"""

import re

import structlog
from django.core.cache import cache

from agentcc.services.gateway_client import GatewayClientError, get_gateway_client

logger = structlog.get_logger(__name__)

MAX_ROWS = 200
MAX_TOP = 12
# Shared with the managed-models console views, which read the same catalog.
CATALOG_CACHE_KEY = "agentcc:managed-models:catalog"
CATALOG_CACHE_SECONDS = 300

# The curated "top" families, in display order. Each pattern is matched on
# the model id's last path segment; the newest version the org can run wins,
# so the list stays current as new versions arrive. Batch-only ids (":batch")
# are never top and never listed.
_V = r"(\d+(?:[.-]\d+)*?)"
_DATE = r"(?:-\d{8})?"
TOP_FAMILIES = (
    re.compile(rf"^gpt-{_V}$"),
    re.compile(rf"^claude-sonnet-{_V}{_DATE}$"),
    re.compile(rf"^gemini-{_V}-pro$"),
    re.compile(rf"^claude-opus-{_V}{_DATE}$"),
    re.compile(rf"^gpt-{_V}-mini$"),
    re.compile(rf"^gemini-{_V}-flash$"),
    re.compile(rf"^claude-haiku-{_V}{_DATE}$"),
    re.compile(rf"^grok-{_V}$"),
    re.compile(rf"^deepseek-v{_V}$"),
    re.compile(rf"^kimi-k{_V}$"),
    re.compile(rf"^llama-{_V}-maverick$"),
    re.compile(rf"^mistral-large-{_V}$"),
)


class ConsoleModelsUnavailable(Exception):
    """The list cannot say who would pay: the org's providers or config could
    not be read (a managed row's payer depends on them), or neither source
    could be read."""

    def __init__(self, status=503):
        super().__init__("console_models_unavailable")
        self.status = 502 if status == 502 else 503


def _has_credentials(cfg):
    # agentcc-gateway internal/tenant/config.go ProviderConfig.HasCredentials.
    return bool(
        cfg.get("api_key")
        or cfg.get("service_account_json")
        or (cfg.get("aws_access_key_id") and cfg.get("aws_secret_access_key"))
    )


# A model id the list may show: the gateway's console-run models-cap pattern.
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}")


def _unassembled_providers(org_id, assembled_ids):
    """{provider name: [model ids]} for the org's active provider credentials
    the push assembly left out (an unreadable or refused credential). The
    gateway may still serve them from an earlier push, so the list neither
    lists them nor guesses "managed" for a model they might serve. Read from
    the rows without decrypting anything; used only for matching."""
    from agentcc.models.provider_credential import AgentccProviderCredential

    unsure = {}
    rows = AgentccProviderCredential.no_workspace_objects.filter(
        organization_id=org_id, is_active=True, deleted=False
    ).values_list("provider_name", "models_list")
    for provider_name, models in rows:
        if not isinstance(provider_name, str) or provider_name in assembled_ids:
            continue
        ids = []
        for entry in models if isinstance(models, list) else []:
            value = entry.get("id") if isinstance(entry, dict) else entry
            if isinstance(value, str) and value:
                ids.append(value)
        unsure.setdefault(provider_name, []).extend(ids)
    return unsure


def _usable_org_providers(org_id):
    """({provider id: [model ids]}, {provider: [model ids]}): the org's pushed,
    usable providers and the active providers the assembly left out.

    Uses the exact assembly FA pushes to the gateway, so a row is byok only
    when the gateway would run it on the org's key. The list emits only model
    ids that came through that assembly's credential-aware projection (the one
    FA's own public provider views use) and gateway catalog ids; provider ids
    and model-map aliases are used for routing only and never emitted (a
    provider is shown by a fixed label, ``_provider_label``). Credentials stay
    in the assembly: only provider ids and projected model ids leave here.
    """
    from agentcc.services.config_push import _assemble_providers

    assembled = _assemble_providers(org_id) or {}
    usable = {}
    for provider_id, cfg in assembled.items():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("enabled") is not True or not _has_credentials(cfg):
            continue
        models = cfg.get("models") or []
        usable[str(provider_id)] = [m for m in models if isinstance(m, str) and m]
    unsure = _unassembled_providers(org_id, set(assembled))
    return usable, unsure


def _provider_label(provider_id):
    """A provider as shown: its name when it is a provider FA knows, else
    "custom". An org can name a custom provider anything, so the name itself is
    never emitted."""
    from agentcc.services.provider_validation import KNOWN_PROVIDER_NAMES

    return provider_id if provider_id in KNOWN_PROVIDER_NAMES else "custom"


def _org_settings(organization):
    """(credits on, model map) from the org's active config."""
    from agentcc.models.org_config import AgentccOrgConfig

    active = AgentccOrgConfig.no_workspace_objects.filter(
        organization=organization, is_active=True, deleted=False
    ).first()
    if active is None:
        return False, {}
    credits_on = bool((active.managed_models or {}).get("enabled"))
    model_map = active.model_map if isinstance(active.model_map, dict) else {}
    return credits_on, model_map


# Unicode simple case folding pairs an ASCII letter with a non-ASCII one only
# for k (KELVIN SIGN) and s (LATIN SMALL LETTER LONG S).
_ASCII_FOLD_PARTNERS = {"k": "\u212a", "s": "\u017f"}


def _fold_set(char):
    """``char`` and every single character its lowercase, uppercase and case
    fold lead to: its case orbit (Go's unicode.SimpleFold), for two non-ASCII
    characters."""
    seen, todo = {char}, [char]
    while todo:
        current = todo.pop()
        for other in (current.lower(), current.upper(), current.casefold()):
            if len(other) == 1 and other not in seen:
                seen.add(other)
                todo.append(other)
    return seen


def _chars_fold(a, b):
    if a == b:
        return True
    if a.isascii() and b.isascii():
        return a.lower() == b.lower()
    if a.isascii() or b.isascii():
        ascii_char, other = (a, b) if a.isascii() else (b, a)
        return _ASCII_FOLD_PARTNERS.get(ascii_char.lower()) == other
    return bool(_fold_set(a) & _fold_set(b))


def _equal_fold(left, right):
    """Go's strings.EqualFold(left, right), which the gateway uses for a
    provider prefix: simple case folding, one character for one character, so
    the fi ligature is not "fi" and sharp s is not "ss". Exact for every
    character pair with an ASCII side (the gateway pins those rows in
    org_route_prefix_fold_test.go); every id the list shows is ASCII
    (``_showable``), so the payer prediction is always on that path. Two
    non-ASCII characters compare by their case orbit, which only picks a
    listed id's catalog details."""
    return len(left) == len(right) and all(
        _chars_fold(a, b) for a, b in zip(left, right, strict=True)
    )


def _redirected(model, providers, model_map):
    """True when the model map sends ``model`` to a different model: its
    mapped provider is usable and lists exactly one other model (handlers.go
    orgMappedRoute). A Console run refuses that (403 model_not_permitted), so
    such a model is not listed."""
    mapped = model_map.get(model)
    if not isinstance(mapped, str) or mapped not in providers:
        return False
    listed = providers[mapped]
    return len(listed) == 1 and not _model_matches(listed[0], model, mapped)


def _model_matches(listed, requested, provider_id):
    # handlers.go orgModelMatches.
    if listed == requested:
        return True
    prefix, sep, bare = requested.partition("/")
    return bool(sep and prefix) and _equal_fold(prefix, provider_id) and listed == bare


def _byok_provider(model, providers, model_map):
    """The provider the run path would serve ``model`` on with the org's key,
    in orgRouteFor's order: model map, then the provider the id's prefix names
    (orgPrefixProvider), then any provider that lists it. Provider ids are
    compared in byte order, as the gateway does."""
    mapped = model_map.get(model)
    if isinstance(mapped, str) and mapped in providers:
        return mapped
    prefix, sep, _ = model.partition("/")
    if sep and prefix:
        for provider_id in sorted(providers):
            if _equal_fold(prefix, provider_id) and any(
                _model_matches(m, model, provider_id) for m in providers[provider_id]
            ):
                return provider_id
    for provider_id in sorted(providers):
        if any(_model_matches(m, model, provider_id) for m in providers[provider_id]):
            return provider_id
    return None


def _source_error(error):
    return {"code": error.code, "message": error.public_message}


def _catalog(client):
    # The cache is an optimisation: a cache outage reads the gateway instead.
    try:
        data = cache.get(CATALOG_CACHE_KEY)
    except Exception:
        data = None
    if data is None:
        data = client.get_catalog()
        try:
            cache.set(CATALOG_CACHE_KEY, data, CATALOG_CACHE_SECONDS)
        except Exception:
            logger.info("console_models_catalog_cache_unavailable")
    return data


def _number(value):
    return (
        value
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else None
    )


def _row(model, name, provider, vendor, billing_mode, payers, prices, context_length):
    input_price, output_price = prices
    # A price of 0 would read as free on a credits row: priced means both
    # prices are known and above zero.
    priced = bool(input_price and output_price and input_price > 0 and output_price > 0)
    return {
        "model": model,
        "name": name or model,
        "provider": provider,
        "vendor": vendor or None,
        "billing_mode": billing_mode,
        "payers": payers,
        "priced": priced,
        "input_per_mtok_usd": input_price if priced else None,
        "output_per_mtok_usd": output_price if priced else None,
        "cached_input_per_mtok_usd": None,
        "context_length": context_length if isinstance(context_length, int) else None,
        "top": False,
        "top_rank": None,
    }


def _version(text):
    return tuple(int(part) for part in re.split(r"[.-]", text) if part.isdigit())


def _mark_top(rows):
    picked = []
    for family in TOP_FAMILIES:
        best = None
        for row in rows:
            match = family.match(row["model"].rsplit("/", 1)[-1])
            if not match or row in picked:
                continue
            version = _version(match.group(1))
            if best is None or version > best[0]:
                best = (version, row)
        if best is not None:
            picked.append(best[1])
        if len(picked) == MAX_TOP:
            break
    for rank, row in enumerate(picked, start=1):
        row["top"] = True
        row["top_rank"] = rank


def list_console_models(organization, workspace=None):
    """The org's Console model list (contract r4). ``workspace`` is accepted
    for the callers' context; providers and credits are org-level."""
    org_id = str(organization.id)
    client = get_gateway_client()

    # Who pays for a managed row depends on the org's own providers and model
    # map: without them the list cannot say, so either failing is unavailable.
    try:
        credits_on, model_map = _org_settings(organization)
        providers, unsure = _usable_org_providers(org_id)
    except Exception:
        logger.warning("console_models_org_unavailable", org_id=org_id)
        raise ConsoleModelsUnavailable(503) from None

    managed = {
        "available": False,
        "enabled": credits_on,
        "balance_usd": None,
        "markup_percent": None,
        "error": None,
    }
    catalog_models = []
    try:
        credits = client.get_org_credits(org_id)
        managed.update(
            available=True,
            balance_usd=_number(credits.get("balance_usd")),
            markup_percent=_number(credits.get("markup_percent")),
        )
        catalog = _catalog(client)
        if catalog.get("available") is False:
            managed["available"] = False
        else:
            catalog_models = [
                m for m in catalog.get("models") or [] if isinstance(m, dict)
            ]
    except GatewayClientError as error:
        if not (error.status_code == 404 and error.code == "managed_models_disabled"):
            managed["available"] = False
            managed["error"] = _source_error(error)
        else:
            managed["available"] = False

    catalog_by_id = {
        m.get("id"): m for m in catalog_models if isinstance(m.get("id"), str)
    }
    # "<provider>/<model>" catalog ids by their exact model part, for a listed
    # bare id's details: the provider part folds, the model part is exact.
    catalog_by_model = {}
    for key in sorted(catalog_by_id):
        prefix, sep, bare = key.partition("/")
        if sep and prefix and bare:
            catalog_by_model.setdefault(bare, []).append((prefix, catalog_by_id[key]))

    def prefixed_details(model, provider_id):
        for prefix, details in catalog_by_model.get(model, ()):
            if _equal_fold(prefix, provider_id):
                return details
        return None

    rows = {}
    # byok: listed (projected) models; model-map aliases route but are not listed.
    listed = {}
    for provider_id in sorted(providers):
        for model in providers[provider_id]:
            listed.setdefault(model, provider_id)
    for model, _ in listed.items():
        if ":batch" in model or not _showable(model):
            continue
        if _redirected(model, providers, model_map):
            continue  # the run path would send another model; a Console run refuses it
        provider_id = _byok_provider(model, providers, model_map)
        if provider_id is None:
            continue
        info = catalog_by_id.get(model) or prefixed_details(model, provider_id) or {}
        payers = (
            ["byok", "managed"]
            if (
                credits_on
                and model in catalog_by_id
                and _billed(catalog_by_id[model]) is not None
            )
            else ["byok"]
        )
        rows[model] = _row(
            model,
            info.get("name"),
            _provider_label(provider_id),
            info.get("vendor"),
            "byok",
            payers,
            (_number(info.get("input_per_mtok")), _number(info.get("output_per_mtok"))),
            info.get("context_length"),
        )

    # managed: priced catalog models the org's key does not already serve.
    if credits_on and managed["available"]:
        for model_id, info in catalog_by_id.items():
            if ":batch" in model_id or model_id in rows or not _showable(model_id):
                continue
            if _redirected(model_id, providers, model_map):
                continue
            billed = _billed(info)
            if billed is None:
                continue
            if model_map.get(model_id) in unsure or (
                _byok_provider(model_id, unsure, {}) is not None
            ):
                continue  # a provider FA could not assemble might serve it
            provider_id = _byok_provider(model_id, providers, model_map)
            if provider_id is not None:
                # Distinct ids are distinct rows, even when one is the
                # provider-prefixed form of a listed bare id.
                rows[model_id] = _row(
                    model_id,
                    info.get("name"),
                    _provider_label(provider_id),
                    info.get("vendor"),
                    "byok",
                    ["byok", "managed"],
                    (
                        _number(info.get("input_per_mtok")),
                        _number(info.get("output_per_mtok")),
                    ),
                    info.get("context_length"),
                )
                continue
            rows[model_id] = _row(
                model_id,
                info.get("name"),
                str(info.get("provider") or ""),
                info.get("vendor"),
                "managed",
                ["managed"],
                billed,
                info.get("context_length"),
            )

    ordered = list(rows.values())
    _mark_top(ordered)
    ordered.sort(
        key=lambda r: (
            0 if r["top"] else 1,
            r["top_rank"] or 0,
            0 if r["billing_mode"] == "byok" else 1,
            (r["vendor"] or "").lower(),
            r["name"].lower(),
            r["model"],
        )
    )
    return {
        "sources": {
            "byok": {
                "available": bool(providers),
                "providers": sorted({_provider_label(p) for p in providers}),
                "error": None,
            },
            "managed": managed,
        },
        "models": ordered[:MAX_ROWS],
        "total": len(ordered),
        "truncated": len(ordered) > MAX_ROWS,
    }


def _billed(info):
    billed_in = _number(info.get("billed_input_per_mtok"))
    billed_out = _number(info.get("billed_output_per_mtok"))
    if billed_in is None or billed_out is None or billed_in <= 0 or billed_out <= 0:
        return None
    return billed_in, billed_out


# Router ids let a router choose the model; the console-run grant refuses them.
_ROUTER_NAMES = frozenset({"auto", "agentcc/auto", "omega-default", "omega/auto"})


def _showable(model):
    """Every row's model meets one pattern, the one the gateway's console-run
    grant accepts for its models cap, and is not a router id (last path
    segment "auto" in any case, or the gateway's own auto names): a row the run
    path could not carry is not listed."""
    if not _MODEL_ID.fullmatch(model) or "://" in model:
        return False
    if model.rsplit("/", 1)[-1].casefold() == "auto":
        return False
    return model.casefold() not in _ROUTER_NAMES
