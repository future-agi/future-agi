"""Where an org guardrail config may not hold "${": a check's credentials, URLs
and other provider settings. The gateway refuses any ``${`` there too (the
check fails closed and makes no call), in two kinds of org check:

- a futureagi check (config ``provider: futureagi``): its ``api_key``,
  ``secret_key`` and ``base_url``;
- an external-provider check (config ``provider`` one of
  ``EXTERNAL_PROVIDERS``): every top-level string value of its config.

A check's kind is read the way the gateway's org-check factory reads it
(agentcc-gateway internal/guardrails/orgfactory): a built-in check name first,
then provider futureagi, a ``url`` (webhook), an ``expression``, tool
permissions and MCP security, and only then an external provider. The check
name is the one the push sends (``_RULE_TO_REGISTRY``), and a rule that names
no provider gets the one the push gives it (``_RULE_PROVIDER_DEFAULTS``),
whatever shape it is stored in: a policy sync turns any stored check into the
rules shape.

Every other value in a guardrails section is plain text and is not refused:
a blocklist word (``${word}``), a regex, an expression, a webhook url or
header, an adapter's nested list, a check's name, a map key and
``tool_calls`` keep any ``${`` they hold.

Two checks use these sets:

- ``reject_guardrail_env_references``: what a save sends, on every path that
  saves a guardrail check's config, switched on or off. It runs on the raw
  section, before a credential becomes the "__encrypted__" sentinel.
- ``reject_pushed_guardrail_env_references``: what a version would push,
  credentials decrypted, in the checks that are switched on (the gateway
  never builds one that is off). It runs on an activation, and on a save that
  changes the guardrails section, where it refuses only a check the save
  newly switches on or changes. A check is exempt only when the active
  version already pushes it exactly as the save would (its whole pushed
  entry, apart from ``enabled``): that check is not the save's doing, and
  refusing it would stop an org with two such checks from switching either
  off. A save that keeps such a value but changes anything else about its
  check (an endpoint, a provider, an action) is refused. An active version the
  push cannot transform holds nothing, so a save there is checked in full and a
  save that repairs it goes ahead.
"""

from collections.abc import Mapping

import structlog
from pydantic import ValidationError

from agentcc.services.config_push import (
    _RULE_PROVIDER_DEFAULTS,
    _RULE_TO_REGISTRY,
    OrgConfigRejected,
    pushed_guardrails,
)

logger = structlog.get_logger(__name__)

# The check names the gateway's org-check factory builds as built-ins,
# whatever their config holds (none of them reads the environment).
GATEWAY_BUILTIN_CHECKS = frozenset(
    {
        "pii-detection",
        "content-moderation",
        "keyword-blocklist",
        "input-validation",
        "prompt-injection",
        "secret-detection",
        "topic-restriction",
        "language-detection",
        "system-prompt-protection",
        "hallucination-detection",
        "data-leakage-prevention",
    }
)
# external.IsExternalProviderConfig's providers.
EXTERNAL_PROVIDERS = frozenset(
    {
        "lakera",
        "azure_content_safety",
        "presidio",
        "llama_guard",
        "bedrock_guardrails",
        "hiddenlayer",
        "dynamoai",
        "enkrypt",
        "ibm_ai",
        "pangea",
        "zscaler",
        "crowdstrike",
        "aporia",
        "lasso",
        "grayswan",
    }
)
# The futureagi check's fields that may not hold "${".
FUTUREAGI_EXPANDED_FIELDS = ("api_key", "secret_key", "base_url")


class GuardrailEnvReference(OrgConfigRejected):
    """A guardrail config string that may not hold "${" holds one.

    The gateway refuses such a check too. Only an operator's config.yaml rule
    may use "${VAR}"; an org config gives the value itself.
    """

    code = "guardrail_env_reference"

    def __init__(self, path):
        self.param = path
        super().__init__(
            f"{path} contains an environment reference (${{...}}), which an "
            "organization guardrail config may not use in a credential, URL or "
            "other provider setting. Set the value itself; ${VAR} expansion is "
            "for the gateway's own config file only."
        )


def expanded_fields(name, cfg):
    """The keys of ``cfg`` that may not hold "${" (credentials, URLs and other
    provider settings), for a check sent under ``name``. Empty for every other
    check kind."""
    if not isinstance(cfg, Mapping):
        return ()
    if not isinstance(name, str):
        name = None
    if _RULE_TO_REGISTRY.get(name, name) in GATEWAY_BUILTIN_CHECKS:
        return ()
    provider = (
        cfg["provider"] if "provider" in cfg else _RULE_PROVIDER_DEFAULTS.get(name)
    )
    if not isinstance(provider, str):
        provider = None
    if provider == "futureagi":
        return tuple(field for field in FUTUREAGI_EXPANDED_FIELDS if field in cfg)
    # A webhook, an expression, tool permissions and MCP security come before
    # an external provider, and none reads the environment.
    if isinstance(cfg.get("url"), str) or isinstance(cfg.get("expression"), str):
        return ()
    if provider not in EXTERNAL_PROVIDERS:
        return ()
    return tuple(sorted(key for key, value in cfg.items() if isinstance(value, str)))


def _has_env_reference(value):
    return isinstance(value, str) and "${" in value


def _section_checks(section, path):
    """(path, name, check) for every check in a stored or sent guardrails
    section: its rules list, and its checks as a list or as a map keyed by
    name."""
    if not isinstance(section, Mapping):
        return
    rules = section.get("rules")
    if isinstance(rules, list):
        for i, check in enumerate(rules):
            if isinstance(check, Mapping):
                yield f"{path}.rules[{i}]", check.get("name"), check
    checks = section.get("checks")
    if isinstance(checks, list):
        yield from _list_checks(checks, f"{path}.checks")
    elif isinstance(checks, Mapping):
        for name, check in checks.items():
            if isinstance(check, Mapping):
                yield f"{path}.checks.{name}", name, check


def _list_checks(checks, path):
    for i, check in enumerate(checks):
        if isinstance(check, Mapping):
            yield f"{path}[{i}]", check.get("name"), check


def _first_env_reference(items):
    for check_path, name, check in items:
        cfg = check.get("config")
        for field in expanded_fields(name, cfg):
            if _has_env_reference(cfg[field]):
                return f"{check_path}.config.{field}"
    return None


def guardrail_env_reference_path(section, path="guardrails"):
    """The first field of a guardrails section (any stored shape) that may not
    hold "${" and holds it, or None."""
    return _first_env_reference(_section_checks(section, path))


def policy_checks_env_reference_path(checks, path="checks"):
    """As guardrail_env_reference_path, for a guardrail policy's checks list
    (which a policy sync pushes as a checks list)."""
    if not isinstance(checks, list):
        return None
    return _first_env_reference(_list_checks(checks, path))


def rule_env_reference_path(name, rule, path="config"):
    """As guardrail_env_reference_path, for one rule an update-guardrail
    request sends as ``{"name": name, "config": rule}``: the path names the
    request's field, e.g. ``config.config.api_key``."""
    if not isinstance(rule, Mapping):
        return None
    return _first_env_reference([(path, name, rule)])


def reject_guardrail_env_references(section, path="guardrails"):
    """Refuse a guardrails section a save sends that holds "${" in a field that
    may not hold it. Raises GuardrailEnvReference naming the field, never the
    value. Run it on the raw section, before a credential becomes the
    "__encrypted__" sentinel."""
    found = guardrail_env_reference_path(section, path)
    if found:
        raise GuardrailEnvReference(found)


def reject_rule_env_references(name, rule):
    """reject_guardrail_env_references for update-guardrail's one rule."""
    found = rule_env_reference_path(name, rule)
    if found:
        raise GuardrailEnvReference(found)


def _pushed_checks(config):
    """{check name: check} for every check a push of ``config`` switches on,
    as the gateway contract dumps it (credentials decrypted). A version the
    contract cannot read pushes nothing."""
    if config is None:
        return {}
    try:
        section = pushed_guardrails(config)
    except ValidationError:
        return {}
    checks = section.get("checks") if isinstance(section, Mapping) else None
    return {
        name: check
        for name, check in (checks or {}).items()
        if isinstance(check, Mapping) and check.get("enabled") is True
    }


def _env_reference_fields(name, check):
    """The sorted fields of a pushed check that may not hold "${" and hold
    it."""
    cfg = check.get("config")
    return sorted(
        field for field in expanded_fields(name, cfg) if _has_env_reference(cfg[field])
    )


def _held_checks(active):
    """The switched-on checks the active version pushes. One the push cannot
    transform at all (a check config stored as a string, say) holds nothing,
    whatever the error: refusing every guardrails save there would also
    refuse the save that repairs it."""
    try:
        return _pushed_checks(active)
    except Exception as exc:
        logger.warning(
            "guardrail_env_held_unreadable",
            org_id=str(active.organization_id),
            error_type=type(exc).__name__,
        )
        return {}


def _without_enabled(check):
    return {key: value for key, value in check.items() if key != "enabled"}


def reject_pushed_guardrail_env_references(config, active=None):
    """Refuse a version whose push would switch on a check holding "${" in a
    field that may not hold it, credentials decrypted.

    With ``active`` (a save that changes the guardrails section), a check the
    active version already pushes exactly as this version would (its whole
    pushed entry apart from ``enabled``) is left alone; any other such check
    is refused, including one that keeps a held value but changes its
    endpoint, provider, action or any other setting. Without ``active`` (an
    activation), every such check is. Raises GuardrailEnvReference naming
    ``guardrails.checks.<name>.config.<field>``, never the value.
    """
    held = _held_checks(active) if active is not None else {}
    for name, check in sorted(_pushed_checks(config).items()):
        fields = _env_reference_fields(name, check)
        if not fields:
            continue
        kept = held.get(name)
        if kept is not None and _without_enabled(kept) == _without_enabled(check):
            continue
        raise GuardrailEnvReference(f"guardrails.checks.{name}.config.{fields[0]}")
