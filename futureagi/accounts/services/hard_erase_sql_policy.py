"""Name-based raw SQL refusal (ruling 349), not a general SQL sandbox.

Any protected identifier in a mutating statement requires the typed boundary.
No target/alias/SET grammar is inferred. Trusted external DB administrators remain
outside application enforcement; ordinary ORM writes do not acquire lifecycle locks.
"""

import json
import re
from pathlib import Path

from accounts.services.hard_erase_sql_lex import _lex

CREDENTIAL_INVENTORY = json.loads(
    Path(__file__).with_name("hard_erase_credential_inventory.json").read_text()
)
INVENTORIED_MODELS = {(row["module"], row["model"]) for row in CREDENTIAL_INVENTORY}

# Python model classes, never Django app labels (agentcc's label is prism).
PROTECTED_MODELS = frozenset(
    {
        ("django.contrib.auth.models", "Group"),
        ("django.contrib.auth.models", "Permission"),
        ("ee.usage.models.billing_ledger", "LedgerServiceCredential"),
        ("ee.usage.models.billing_ledger", "LedgerCredentialBinding"),
        ("accounts.models.auth_token", "AuthToken"),
        ("accounts.models.organization", "Organization"),
        ("accounts.models.organization_invite", "OrganizationInvite"),
        ("accounts.models.organization_membership", "OrganizationMembership"),
        ("accounts.models.recovery_code", "RecoveryCode"),
        ("accounts.models.service_grant", "ServiceGrant"),
        ("accounts.models.totp_device", "UserTOTPDevice"),
        ("accounts.models.user", "OrgApiKey"),
        ("accounts.models.user", "User"),
        ("accounts.models.webauthn_credential", "WebAuthnCredential"),
        ("accounts.models.workspace", "Workspace"),
        ("accounts.models.workspace", "WorkspaceMembership"),
        ("agentcc.models.api_key", "AgentccAPIKey"),
        ("agentcc.models.delegated_grant", "AgentccDelegatedGrant"),
        ("agentcc.models.managed_upstream_key", "AgentccManagedUpstreamKey"),
        ("agentcc.models.mcp_credential", "AgentccMCPCredential"),
        ("agentcc.models.mcp_oauth", "AgentccMCPOAuthClient"),
        ("agentcc.models.mcp_oauth", "AgentccMCPOAuthConsent"),
        ("agentcc.models.mcp_oauth", "AgentccMCPOAuthRefreshToken"),
        ("agentcc.models.mcp_oauth", "AgentccMCPOAuthSigningKey"),
        ("agentcc.models.mcp_service_provisioning", "AgentccEEServiceCredential"),
        ("agentcc.models.mcp_service_provisioning", "AgentccEEAgentKey"),
        ("agentcc.models.mcp_upstream_oauth", "AgentccMCPConnectState"),
        ("agentcc.models.mcp_upstream_oauth", "AgentccMCPPersonalKey"),
        ("agentcc.models.mcp_upstream_oauth", "AgentccMCPUpstreamClient"),
        ("agentcc.models.provider_credential", "AgentccProviderCredential"),
        # Authorization code is a credential despite its generic `code` field.
        ("mcp_server.models.oauth_code", "MCPOAuthCode"),
        ("model_hub.models.api_key", "ApiKey"),
        ("simulate.models.agent_definition", "ProviderCredentials"),
        ("simulate.models.harness_environment_credentials", "HarnessCredentialFile"),
        (
            "simulate.models.harness_environment_credentials",
            "HarnessEnvironmentCredentials",
        ),
    }
)


# Reviewed noncredential homonyms, keyed by actual db_table.
# Only the individually reviewed homonym fields below are excluded. New fields
# and encrypted field types always re-enter the census, even on an excluded table.
CREDENTIAL_EXCLUSIONS = {
    "agentcc_request_attempt": "Attempt telemetry: key identifiers and token counters, not secret material.",
    "agentcc_request_log": "Request telemetry: key/session identifiers, token counters and identity-source labels, not credentials.",
    "agentcc_session": "Correlation session identifier, not an authentication session.",
    "agentcc_shadow_result": "Comparison token usage counters, not access tokens.",
    "agentcc_tool_call": "Tool telemetry: session/key identifiers, credential-kind and identity-source labels, not credentials.",
    "model_hub_cell": "Dataset cell prompt/completion token counts, not credentials.",
    "model_hub_queueitem": "Trace-session relation for annotation, not an authentication session.",
    "model_hub_row_source": "Trace-session lineage identifier, not an authentication session.",
    "model_hub_score": "Trace-session relation for evaluation, not an authentication session.",
    "simulate_hosted_harness_scenario": "Scenario identifiers and search keywords, not secret keys.",
    "trace_session_overlay": "Trace-session correlation identifier, not authentication state.",
    "tracer_eval_logger": "Trace-session evaluation relation, not authentication state.",
    "tracer_observation_span": "Trace prompt/completion/total token counts, not credentials.",
    "tracer_project": "Observability session configuration, not authentication sessions.",
    "tracer_trace": "Trace-session correlation relation, not authentication state.",
    "tracer_trace_grouping_call": "Request identifier and token counters, not credentials.",
    "tracer_trace_grouping_decision": "Idempotency identifier, not authentication material.",
    "tracer_trace_grouping_feature": "Clustering bucket keys, not cryptographic keys.",
    "tracer_trace_investigation_gateway_call": "Token usage counters, not access tokens.",
    "tracer_trace_investigation_report": "Idempotency identifier and token usage counts, not credentials.",
    "usage_apicalllog": "Usage telemetry input_token_count, not access tokens.",
    "usage_usageeventrecord": "Usage receipt API-key identifier, not authentication material.",
    "webhooks_event": "Webhook source deduplication key, not webhook signing material.",
}
EXCLUDED_FIELDS = {
    "agentcc_request_attempt": (
        "upstream_key_id",
        "credential_type",
        "prompt_tokens",
        "completion_tokens",
        "cached_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
    ),
    "agentcc_request_log": (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "api_key_id",
        "session_id",
        "credential_type",
        "upstream_key_id",
        "cached_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "agent_identity_source",
    ),
    "agentcc_session": ("session_id",),
    "agentcc_shadow_result": ("source_tokens", "shadow_tokens"),
    "agentcc_tool_call": (
        "api_key_id",
        "session_hash",
        "session_id",
        "credential_type",
        "credential_source",
        "agent_identity_source",
    ),
    "model_hub_cell": ("prompt_tokens", "completion_tokens"),
    "model_hub_queueitem": ("trace_session",),
    "model_hub_row_source": ("session_id", "session_missing"),
    "model_hub_score": ("trace_session",),
    "simulate_hosted_harness_scenario": ("scenario_key", "keywords"),
    "trace_session_overlay": ("trace_session_id",),
    "tracer_eval_logger": ("trace_session",),
    "tracer_observation_span": ("prompt_tokens", "completion_tokens", "total_tokens"),
    "tracer_project": ("session_config",),
    "tracer_trace": ("session",),
    "tracer_trace_grouping_call": ("request_key", "input_tokens", "output_tokens"),
    "tracer_trace_grouping_decision": ("idempotency_key",),
    "tracer_trace_grouping_feature": ("bucket_keys",),
    "tracer_trace_investigation_gateway_call": (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "reasoning_output_tokens",
    ),
    "tracer_trace_investigation_report": (
        "idempotency_key",
        "input_tokens",
        "output_tokens",
    ),
    "usage_apicalllog": ("input_token_count",),
    "usage_usageeventrecord": ("api_key_id",),
    "webhooks_event": ("source_key",),
}
IDENTITY_MODEL = re.compile(
    r"membership|grant|credential|identity|permission|authorization", re.I
)
CREDENTIAL_FIELD = re.compile(
    r"token|secret|key|password|credential|oauth|signing|bearer|session|identity|membership|grant|authorization",
    re.I,
)


def encrypted_field(field):
    """Manual encryption is storage metadata regardless of Django field type."""
    return (
        "encrypt" in field.name.lower()
        or "encrypt" in str(getattr(field, "db_column", "")).lower()
        or any("encrypt" in cls.__name__.lower() for cls in type(field).__mro__)
    )


def credential_field(field):
    return bool(
        CREDENTIAL_FIELD.search(field.name)
        or CREDENTIAL_FIELD.search(str(getattr(field, "verbose_name", "")))
        or encrypted_field(field)
    )


def model_requires_protection(module, name, table, fields):
    """Shared census rule; field exclusions never override encrypted storage."""
    if (module, name) in PROTECTED_MODELS | INVENTORIED_MODELS or IDENTITY_MODEL.search(
        name
    ):
        return True
    excluded = EXCLUDED_FIELDS.get(table.lower(), ())
    return any(
        encrypted_field(field)
        or (credential_field(field) and field.name not in excluded)
        for field in fields
    )


def protected_models(models):
    """Census every concrete registered model, including inherited fields."""
    models = [
        model
        for model in models
        if not getattr(model._meta, "abstract", False)
        and not getattr(model._meta, "proxy", False)
    ]
    selected = {
        model
        for model in models
        if model_requires_protection(
            model.__module__,
            model.__name__,
            model._meta.db_table,
            getattr(model._meta, "concrete_fields", ()),
        )
    }
    selected.update(model for model in models if model._meta.auto_created in selected)
    return selected


# Keywords are inspected everywhere, not just at the first token. Quoted
# identifiers and data literals do not introduce executable keywords.
NON_READ = frozenset(
    "delete update insert merge truncate alter drop create grant revoke prepare "
    "execute deallocate call do copy lock vacuum cluster reindex comment security "
    "analyze analyse into share refresh".split()
)
READ_START = frozenset({"select", "with", "values", "table"})


def effective_statement(words):
    """Unwrap statement prefixes, not keywords inside an INSERT conflict clause."""
    index = 0
    while index < len(words):
        value, quoted = words[index]
        if quoted:
            return value
        if value == "prepare":
            # PREPARE name [(type,...)] AS statement; ignore the name/types.
            depth = 0
            for offset in range(index + 1, len(words)):
                part, quoted = words[offset]
                if not quoted:
                    depth += (part == "(") - (part == ")")
                    if part == "as" and depth == 0:
                        index = offset + 1
                        break
            else:
                raise ValueError("unparseable PREPARE statement")
        elif value == "explain":
            index += 1
            # Consume only EXPLAIN's option prefix. Searching ahead for a known
            # statement keyword loses TABLE/new syntax or misclassifies an
            # unknown statement containing a later SELECT as a pure read.
            if index < len(words) and words[index] == ("(", False):
                depth = 0
                while index < len(words):
                    part, is_quoted = words[index]
                    if not is_quoted:
                        depth += (part == "(") - (part == ")")
                    index += 1
                    if depth == 0:
                        break
                if depth:
                    raise ValueError("unparseable EXPLAIN options")
            else:
                while index < len(words) and words[index] in {
                    ("analyze", False),
                    ("analyse", False),  # PostgreSQL alternate ANALYZE spelling.
                    ("verbose", False),
                }:
                    index += 1
        else:
            return value
    raise ValueError("missing wrapped SQL statement")


def protected_mutations(sql, tables):
    """Return refusals for non-pure reads naming any protected identifier.

    EXECUTE/CALL/DO refuse even without a protected name. A sentinel table name
    makes them take the same connection-bound erase/migration authority path.
    Protected EXPLAIN is allowed only in bare form immediately around a pure
    read. All option prefixes (including quoted options) require authority.
    """
    if not isinstance(sql, str):
        raise ValueError("opaque Django SQL cannot be qualified")
    protected = {name.lower() for name in tables}
    found = []
    for words in _lex(sql):
        if not words:
            continue
        first, quoted = words[0]
        keywords = {
            part
            for value, is_quoted in words
            if not is_quoted
            for part in value.split()
        }
        # Executing statements and their wrappers refuse without table names.
        # ON CONFLICT DO UPDATE/NOTHING is DML, not a standalone DO block;
        # unprotected upserts must retain their ordinary behaviour.
        effective = effective_statement(words)
        if not quoted and effective in {"execute", "call", "do"}:
            found.append(("execute", "<execution>", frozenset()))
            continue
        pure = (
            not quoted
            and not (keywords & NON_READ)
            and (
                first in READ_START
                or (
                    first == "explain"
                    and len(words) > 1
                    # Do not use the unwrapped effective statement here: that
                    # loses option semantics, notably quoted ANALYZE names.
                    and not words[1][1]
                    and words[1][0] in READ_START
                )
            )
        )
        if not pure:
            names = {value.lower() for value, _ in words} & protected
            found.extend((first, name, frozenset()) for name in sorted(names))
    return found
