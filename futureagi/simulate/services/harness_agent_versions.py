"""Agent versions of hosted harness environments: credential sets, preflight, activation.

A version's credential values live only in its side row's encrypted blob, and its
provider keys in its own ``ProviderCredentials`` row. Everything else (diffs, reports,
logs, task inputs) carries names and keyed fingerprints, never a value.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from simulate.models import (
    AgentDefinition,
    AgentVersion,
    HarnessAgentVersionRuntime,
    HostedHarnessJob,
)
from simulate.models.agent_definition import ProviderCredentials
from simulate.serializers.harness_job import CONNECTOR_ALIASES
from simulate.services.harness_credentials import (  # noqa: F401 - re-exported
    agent_version_secret_ref,
    is_credential_file_ref,
    is_names_only_ref,
)
from simulate.services.harness_versioning import diffs, hashing
from simulate.services.hosted_harness import HostedHarnessError

logger = logging.getLogger(__name__)

Status = HarnessAgentVersionRuntime.PreflightStatus
Trigger = HarnessAgentVersionRuntime.Trigger
ProviderType = ProviderCredentials.ProviderType

MAX_CREDENTIAL_FILE_BYTES = 8 * 1024 * 1024
MAX_CREDENTIAL_BLOB_BYTES = 16 * 1024 * 1024
# A new version with the identity of a live row of these statuses is that row.
_NOT_DEDUPED = (Status.FAILED, Status.DISCARDED)
_RETRYABLE = (Status.FAILED, Status.PENDING)
# The environment payload's record of its default's last move by hand.
HAND_MOVE_KEY = "agent_default_hand_move"
# Activations turned down for a reason the environment may want to override: offered.
_OFFERED = frozenset(
    {"default_moved_by_hand", "not_parent_default", "rollback_unavailable"}
)
# How long another environment's request holds a version against a discard when
# nothing recorded is still going to move it.
_WAIT_LIMIT = timedelta(hours=1)
# Kept per version, never inherited by a child.
_OWN_PAYLOAD_KEYS = (
    "legacy_secret_refs",
    "secret_refs",
    "source_access",
    "requested_by_environments",
    "requested_defaults",
    "requested_at",
    "created_for_environment",
    "discarded_by",
)
_PROVIDER_ALIASES = {
    ProviderType.VAPI: CONNECTOR_ALIASES["vapi"],
    ProviderType.RETELL: CONNECTOR_ALIASES["retell"],
    ProviderType.LIVEKIT: CONNECTOR_ALIASES["livekit"],
}
_CONNECTOR_PROVIDERS = {
    connector: provider_type
    for connector, aliases in CONNECTOR_ALIASES.items()
    for provider_type, names in _PROVIDER_ALIASES.items()
    if aliases == names
}
_TRIGGER_LABELS = {
    Trigger.CREATION: "Created",
    Trigger.PUSH: "Source pushed",
    Trigger.UPLOAD: "Source uploaded",
    Trigger.PROMPT: "Prompt edited",
    Trigger.AGENT_PAYLOAD: "Agent settings changed",
    Trigger.CREDENTIALS: "Credentials changed",
    Trigger.PROVIDER_SYNC: "Provider synced",
    Trigger.BACKFILL: "Backfilled",
    Trigger.CONNECT: "Connected",
    Trigger.SYNC: "Synced",
}


@dataclass(frozen=True)
class CredentialFile:
    """An uploaded credential file; its content never reaches a repr or a log."""

    alias: str
    content: bytes = field(repr=False)
    filename: str = "credentials.json"
    content_type: str = "application/json"


@dataclass
class CredentialSet:
    """Runtime variables and files by name; provider keys are kept apart."""

    env: dict[str, str] = field(default_factory=dict, repr=False)
    files: dict[str, CredentialFile] = field(default_factory=dict, repr=False)

    def names(self) -> list[str]:
        return sorted({*self.env, *self.files})


@dataclass(frozen=True)
class PreflightResult:
    status: str
    report: dict[str, Any] | None


def configured_prompt(payload: Mapping[str, Any] | None) -> str:
    """The target prompt a phone or LiveKit payload configures, as the build reads it."""
    agent = (payload or {}).get("agent") or {}
    if str(agent.get("connector") or "") not in {"phone", "livekit"}:
        return ""
    return str((agent.get("config") or {}).get("target_system_prompt") or "").strip()


def own_credential_set(runtime: HarnessAgentVersionRuntime) -> bool:
    """Whether a side row holds a credential set of its own, rather than reading an
    environment's stored secrets as a legacy row does."""
    from simulate.services.harness_versioning.assembly import legacy_runtime

    return not legacy_runtime(runtime)


def version_credential_names(runtime: HarnessAgentVersionRuntime) -> list[str]:
    """Every name a version answers for, its provider keys included; never a value."""
    return sorted(
        {
            *(str(name) for name in runtime.credential_names or []),
            *(str(name) for name in runtime.credential_fingerprints or {}),
        }
    )


_RUN_AGENT_FIELDS = ("connector", "mode", "call_direction", "target_speaks_first")
_RUN_SOURCE_KINDS = (
    HarnessAgentVersionRuntime.SourceKind.GITHUB,
    HarnessAgentVersionRuntime.SourceKind.ARCHIVE,
)


_REDACTED = "[redacted]"
_ABSENT = object()


def _redacted_paths(value: Any, path: tuple = ()) -> Iterable[tuple]:
    if value == _REDACTED:
        yield path
    elif isinstance(value, dict):
        for name, item in value.items():
            yield from _redacted_paths(item, (*path, name))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _redacted_paths(item, (*path, index))


def _at(value: Any, path: tuple) -> Any:
    for step in path:
        if isinstance(value, dict) and step in value:
            value = value[step]
        elif isinstance(value, list) and isinstance(step, int) and step < len(value):
            value = value[step]
        else:
            return _ABSENT
    return value


def _restore_redacted(
    config: dict[str, Any],
    own_config: Any,
    runtime: HarnessAgentVersionRuntime,
    environment_id: Any,
) -> None:
    """Config the version stored redacted takes the run environment's own value."""
    left = []
    for path in list(_redacted_paths(config)):
        own = _at(own_config, path)
        if own not in (_ABSENT, _REDACTED):
            _at(config, path[:-1])[path[-1]] = copy.deepcopy(own)
        else:
            left.append(".".join(str(step) for step in path))
    if left:
        logger.warning(
            "harness_agent_version_config_redacted version=%s environment=%s paths=%s",
            runtime.agent_version_id,
            environment_id,
            left,
        )


def apply_agent_version(
    payload: dict[str, Any],
    runtime: HarnessAgentVersionRuntime,
    *,
    environment_id: Any = None,
) -> None:
    """Put a version's own agent settings and source into a run's ``payload``.

    The agent's secret refs name the version's credentials only; the resolver answers
    them from its set at launch. A provider, phone or remote target keeps the
    environment's source: the version's descriptor of it is not a fetchable source.
    Config stored redacted is restored from ``payload``, the environment's own.
    """
    stored = runtime.agent_payload or {}
    agent = dict(payload.get("agent") or {})
    own_config = agent.get("config") or {}
    for name in _RUN_AGENT_FIELDS:
        if name in stored or name in agent:
            agent[name] = copy.deepcopy(stored.get(name))
    agent["config"] = copy.deepcopy(stored.get("config") or {})
    _restore_redacted(agent["config"], own_config, runtime, environment_id)
    agent["secret_refs"] = {
        name: agent_version_secret_ref(name)
        for name in version_credential_names(runtime)
    }
    payload["agent"] = agent
    if runtime.source_kind not in _RUN_SOURCE_KINDS:
        return
    current = payload.get("source") or {}
    access = stored.get("source_access") or {}
    source = {
        "kind": str(runtime.source_kind),
        **copy.deepcopy(runtime.source or {}),
        "visibility": access.get("visibility") or "public",
    }
    if access.get("installation_id"):
        source["installation_id"] = access["installation_id"]
    if current.get("environment_values"):
        # Plain settings the environment was built with; not part of the agent.
        source["environment_values"] = copy.deepcopy(current["environment_values"])
    payload["source"] = source


def keeps_credential_names(
    environment: HostedHarnessJob,
    runtime: HarnessAgentVersionRuntime,
    names: Iterable[str],
) -> bool:
    """Whether changing these names over ``runtime``'s set (for a legacy row, over
    ``environment``'s refs) keeps the names its bundles are keyed by."""
    from simulate.services.harness_versioning.backfill import effective_runtime

    held = set(version_credential_names(effective_runtime(environment, runtime)))
    return {str(name) for name in names} <= held


def changed_set_runnable(
    environment: HostedHarnessJob,
    source: HostedHarnessJob,
    parent: Any,
    names: Iterable[str],
) -> bool:
    """Whether ``parent`` with these names changed could run in ``source``'s run (the
    environment's own, or a rerun's): certifying is on, or the names stay and its
    bundle for the run's pair is cached or sealed in the default pair."""
    from simulate.services.harness_versioning.assembly import (
        bundle_ready,
        certify_enabled,
    )
    from simulate.services.harness_versioning.backfill import effective_runtime
    from simulate.services.hosted_harness import replays_default_pair, resolve_run_pins

    if certify_enabled():
        return True
    # The environment's facts: the version is made from them (``create_agent_version``).
    runtime = effective_runtime(
        environment,
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=parent.id
        )
        .defer("credential_blob")
        .first(),
        # A default pinned without a side row runs the environment's own agent.
        version=parent if environment.agent_version_id == parent.id else None,
    )
    if runtime is None or not keeps_credential_names(environment, runtime, names):
        return False
    try:
        pins = resolve_run_pins(
            environment,
            rerun_of=source if source.environment_id is not None else None,
        )
    except HostedHarnessError as exc:
        if exc.code != "version_not_runnable":
            raise
        # Only the defaults' pair refuses; a picked agent is not held to it.
        pins = None
    pair = (
        (pins.environment_version, pins.suite_version)
        if pins is not None
        else (environment.environment_version, environment.suite_version)
    )
    return None not in pair and (
        replays_default_pair(environment, parent.id, *(one.id for one in pair))
        or bundle_ready(environment, runtime, *pair)
    )


def credential_set_readable(runtime: HarnessAgentVersionRuntime) -> bool:
    """Whether a side row's credential set can be decrypted; nothing of it is kept."""
    if not runtime.credential_blob:
        return True
    try:
        credential_set(runtime)
    except HostedHarnessError:
        return False
    return True


def _credential_unavailable() -> HostedHarnessError:
    return HostedHarnessError(
        "credential_unavailable",
        "this agent version's credentials can't be read; save them again",
        status_code=422,
    )


def _too_large(what: str) -> HostedHarnessError:
    return HostedHarnessError(
        "credential_blob_too_large",
        f"{what} is too large to store",
        status_code=413,
    )


def _side_row(version_id: Any, *, lock: bool = False, blob: bool = False):
    rows = HarnessAgentVersionRuntime.no_workspace_objects.filter(
        agent_version_id=version_id
    )
    if lock:
        rows = rows.select_for_update()
    if not blob:
        rows = rows.defer("credential_blob")
    return rows.first()


# --- credential sets ----------------------------------------------------------------


def _encode(credentials: CredentialSet) -> str:
    for one in credentials.files.values():
        if len(one.content) > MAX_CREDENTIAL_FILE_BYTES:
            raise _too_large(f"credential file {one.alias}")
    secret_text = json.dumps(
        {
            "env": dict(sorted(credentials.env.items())),
            "files": [
                {
                    "alias": one.alias,
                    "filename": one.filename,
                    "content_type": one.content_type,
                    "content_b64": base64.b64encode(one.content).decode("ascii"),
                }
                for _, one in sorted(credentials.files.items())
            ],
        },
        separators=(",", ":"),
    )
    if len(secret_text.encode("utf-8")) > MAX_CREDENTIAL_BLOB_BYTES:
        raise _too_large("the credential set")
    return secret_text


# Locals holding values are named for the error tracker's scrubber (``secret``).


def _decoded_set(runtime: HarnessAgentVersionRuntime) -> CredentialSet:
    secret_text = runtime.get_credentials()
    secret_document = json.loads(secret_text) if secret_text else {}
    secret_set = CredentialSet(
        env={
            str(name): str(value)
            for name, value in (secret_document.get("env") or {}).items()
        }
    )
    for secret_item in secret_document.get("files") or []:
        alias = str(secret_item["alias"])
        secret_set.files[alias] = CredentialFile(
            alias=alias,
            content=base64.b64decode(secret_item["content_b64"]),
            filename=str(secret_item.get("filename") or "credentials.json"),
            content_type=str(secret_item.get("content_type") or "application/json"),
        )
    return secret_set


def credential_set(runtime: HarnessAgentVersionRuntime) -> CredentialSet:
    """The decrypted set of a side row; empty for a legacy row."""
    try:
        return _decoded_set(runtime)
    except Exception:  # noqa: BLE001 - nothing of the blob may reach the error
        raise _credential_unavailable() from None


def _own_provider_row(version_id: Any) -> ProviderCredentials | None:
    if version_id is None:
        return None
    return ProviderCredentials.objects.filter(agent_version_id=version_id).first()


def _row_values(row: ProviderCredentials | None) -> dict[str, str]:
    """A provider row's keys by the aliases a run reads them under."""
    if row is None:
        return {}
    if row.provider_type == ProviderType.LIVEKIT:
        secret_values = {
            "LIVEKIT_URL": row.server_url,
            "LIVEKIT_API_KEY": row.get_api_key(),
            "LIVEKIT_API_SECRET": row.get_api_secret(),
        }
    else:
        alias = _PROVIDER_ALIASES.get(row.provider_type, ("VAPI_API_KEY",))[0]
        secret_values = {alias: row.get_api_key()}
    return {alias: value for alias, value in secret_values.items() if value}


def pinned_credential_values(job: HostedHarnessJob) -> dict[str, str] | None:
    """Every value the job's pinned agent version holds, by alias; ``None`` when the job
    is unpinned or pinned to a legacy version, which resolve their refs as before."""
    from simulate.services.harness_versioning.versions import versioning_enabled

    if not job.agent_version_id:
        return None
    if job.environment_id is None and not versioning_enabled():
        # With versioning off an environment's own jobs read its stored refs, as its
        # unpinned runs do; a run pinned before reads the names-only refs it holds.
        return None
    return _version_values(job.organization_id, job.agent_version_id)


def _version_values(organization_id: Any, version_id: Any) -> dict[str, str] | None:
    runtime = (
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=version_id,
            organization_id=organization_id,
        )
        .only("id", "agent_version_id", "credential_blob")
        .first()
    )
    if runtime is None or not runtime.credential_blob:
        return None
    secret_set = credential_set(runtime)
    try:
        # The version's own provider row only: a definition's legacy row is not its.
        pinned_secrets = _flat_values(
            secret_set, _row_values(_own_provider_row(version_id))
        )
    except Exception:  # noqa: BLE001 - nothing of a value may reach the error
        raise _credential_unavailable() from None
    return pinned_secrets


def _flat_values(
    secret_set: Any, provider_secrets: Mapping[str, str], errors: str = "strict"
) -> dict[str, str]:
    """A credential set's variables, files (decoded) and provider values, by name."""
    return {
        **secret_set.env,
        **{
            alias: one.content.decode("utf-8", errors)
            for alias, one in secret_set.files.items()
        },
        **provider_secrets,
    }


def _legacy_values(
    environment: HostedHarnessJob, organization: Any, replaced: Iterable[str] = ()
) -> tuple[CredentialSet, dict[str, str]]:
    """The requesting environment's set, resolved from the stored secrets its refs
    name; a legacy side row is shared by environments whose refs may differ. Names in
    ``replaced`` are given new values, so their stored secrets are not read."""
    refs = ((environment.payload or {}).get("agent") or {}).get("secret_refs") or {}
    return _resolve_refs(organization, refs, replaced)


def _resolve_refs(
    organization: Any, refs: Mapping[str, Mapping[str, Any]], skipped: Iterable[str]
) -> tuple[CredentialSet, dict[str, str]]:
    """The values the stored secrets these refs name hold, as a set and provider keys;
    names in ``skipped`` are not read."""
    from simulate.services.hosted_harness_gateway import resolve_secret_reference

    secret_set = CredentialSet()
    provider_secrets: dict[str, str] = {}
    skipped = set(skipped)
    for alias, ref in sorted(refs.items()):
        if alias in skipped:
            continue
        try:
            secret_value = resolve_secret_reference(organization, alias, ref)
        except HostedHarnessError as exc:
            if exc.code != "secret_not_found":
                raise
            raise HostedHarnessError(
                "credential_missing",
                f"A stored value for {alias} is missing; enter it again",
                status_code=422,
                details={"name": alias},
            ) from None
        if alias in hashing.PROVIDER_CREDENTIAL_ALIASES:
            provider_secrets[alias] = secret_value
        elif is_credential_file_ref(ref):
            secret_set.files[alias] = CredentialFile(
                alias=alias, content=secret_value.encode("utf-8")
            )
        else:
            secret_set.env[alias] = secret_value
    return secret_set, provider_secrets


def _overlay(
    secret_set: CredentialSet,
    provider_secrets: dict[str, str],
    secret_values: Mapping[str, str] | None,
    secret_files: Iterable[CredentialFile] | None,
) -> None:
    for name, secret_value in (secret_values or {}).items():
        name = str(name)
        if name in hashing.PROVIDER_CREDENTIAL_ALIASES:
            provider_secrets[name] = str(secret_value)
            continue
        secret_set.files.pop(name, None)
        secret_set.env[name] = str(secret_value)
    for one in secret_files or ():
        secret_set.env.pop(one.alias, None)
        secret_set.files[one.alias] = one


def _fingerprints(
    organization_id: Any,
    secret_set: CredentialSet,
    provider_secrets: Mapping[str, str],
) -> dict[str, str]:
    found = {
        name: hashing.credential_fingerprint(organization_id, secret_value)
        for name, secret_value in secret_set.env.items()
    }
    found.update(
        {
            alias: hashing.credential_fingerprint(organization_id, one.content)
            for alias, one in secret_set.files.items()
        }
    )
    found.update(
        {
            alias: hashing.credential_fingerprint(organization_id, secret_value)
            for alias, secret_value in provider_secrets.items()
        }
    )
    return dict(sorted(found.items()))


def _family(aliases: Iterable[str], connector: str, current: str | None) -> str | None:
    """The one provider whose keys go to the version's provider row: the connector's,
    else the parent row's, else the first named."""
    named = {
        provider_type
        for provider_type, names in _PROVIDER_ALIASES.items()
        if set(names) & set(aliases)
    }
    for preferred in (_CONNECTOR_PROVIDERS.get(connector), current):
        if preferred in named:
            return preferred
    if named:
        return sorted(named)[0]
    return current or _CONNECTOR_PROVIDERS.get(connector)


def _keep_one_family(
    secret_set: CredentialSet, provider_secrets: dict[str, str], family: str | None
) -> None:
    """Keys of any other provider stay in the blob, by name, instead of being refused."""
    kept = set(_PROVIDER_ALIASES.get(family, ()))
    for alias in sorted(set(provider_secrets) - kept):
        secret_set.files.pop(alias, None)
        secret_set.env[alias] = provider_secrets.pop(alias)


def _provider_input(provider_type: str, secret_values: Mapping[str, str]):
    from simulate.services.types.agent_definition import ProviderCredentialsInput

    if provider_type == ProviderType.LIVEKIT:
        return ProviderCredentialsInput(
            provider="livekit",
            livekit_url=secret_values.get("LIVEKIT_URL"),
            livekit_api_key=secret_values.get("LIVEKIT_API_KEY"),
            livekit_api_secret=secret_values.get("LIVEKIT_API_SECRET"),
        )
    alias = _PROVIDER_ALIASES[provider_type][0]
    return ProviderCredentialsInput(
        provider=str(provider_type), api_key=secret_values.get(alias)
    )


def _store_provider_credentials(
    version: AgentVersion,
    template: ProviderCredentials | None,
    provider_secrets: Mapping[str, str],
    family: str | None,
    *,
    keep_keys: bool,
) -> None:
    """The new version's own provider row: a copy of ``template`` (its keys only when
    ``keep_keys``), then any changed keys. The row exists before syncing, so a
    definition's legacy row is never moved."""
    from simulate.services.agent_definition import sync_provider_credentials

    if template is not None:
        row = ProviderCredentials(
            agent_version=version,
            provider_type=template.provider_type,
            api_key=template.api_key if keep_keys else "",
            api_secret=template.api_secret if keep_keys else "",
            assistant_id=template.assistant_id,
            server_url=template.server_url if keep_keys else "",
            agent_name=template.agent_name,
            config_json=copy.deepcopy(template.config_json),
            max_concurrency=template.max_concurrency,
        )
    elif provider_secrets:
        row = ProviderCredentials(
            agent_version=version, provider_type=family or ProviderType.VAPI
        )
    else:
        return
    row.save()
    if not provider_secrets or dict(provider_secrets) == _row_values(row):
        return
    sync_provider_credentials(
        version, _provider_input(family or row.provider_type, provider_secrets)
    )


# --- create -------------------------------------------------------------------------


def _default_id(environment: HostedHarnessJob) -> str | None:
    return str(environment.agent_version_id) if environment.agent_version_id else None


def legacy_side_row(
    environment: HostedHarnessJob,
    version: AgentVersion,
    trigger: str = Trigger.BACKFILL,
) -> HarnessAgentVersionRuntime:
    """The version's side row; one without is given a legacy row like the backfill's,
    built from ``environment``'s payload."""
    from simulate.services.harness_versioning.backfill import (
        default_models,
        side_row_plan,
    )

    existing, runtime = side_row_plan(default_models(), environment, version)
    if existing is not None:
        return existing
    runtime.trigger = trigger
    try:
        with transaction.atomic():
            runtime.save()
    except IntegrityError:
        # Another build of this agent saved the version's side row first.
        existing = HarnessAgentVersionRuntime._base_manager.filter(
            agent_version_id=version.id
        ).first()
        if existing is None:
            raise
        return existing
    return runtime


def _facts(
    source_kind: str,
    source: Mapping[str, Any],
    prompt_hash: str | None,
    fingerprints: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "source": {"kind": source_kind, **source},
        "prompt_hash": prompt_hash,
        "credential_fingerprints": dict(fingerprints),
    }


def _summary(diff: Mapping[str, Any] | None) -> str:
    if not diff:
        return "First version"
    variables = diff.get("variables") or {}
    parts = [
        "source changed" if diff.get("source") else None,
        "prompt changed" if diff.get("prompt") else None,
        *(
            diffs._count("variable", "variables", verb, variables.get(verb))
            for verb in ("added", "removed", "changed")
        ),
    ]
    text = ", ".join(part for part in parts if part)
    return (text[:1].upper() + text[1:]) if text else "No change"


def _prompt_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def next_version_number(definition_id: Any) -> int:
    """Numbered over every row, soft-deleted or in any workspace; call under the
    definition's lock."""
    top = AgentVersion._base_manager.filter(
        agent_definition_id=definition_id
    ).aggregate(top=Max("version_number"))["top"]
    return (top or 0) + 1


def _runtime_hash(runtime: HarnessAgentVersionRuntime) -> str:
    """A side row's version identity.

    How the source is reached counts too, never the bundle key: a version is never
    shared by environments reaching it through other installations.
    """
    from simulate.services.canonical_json import canonical_digest
    from simulate.services.harness_versioning.assembly import runtime_identity

    return canonical_digest(
        {
            "agent": hashing.agent_hash(runtime_identity(runtime)),
            "source_access": (runtime.agent_payload or {}).get("source_access"),
        }
    )


def create_agent_version(
    definition: AgentDefinition,
    *,
    parent: AgentVersion | None,
    env: HostedHarnessJob | None,
    source: Mapping[str, Any] | None = None,
    prompt: str | None = None,
    agent_payload: Mapping[str, Any] | None = None,
    secret_values: Mapping[str, str] | None = None,
    secret_files: Iterable[CredentialFile] | None = None,
    trigger: str,
    user: Any = None,
    activate: bool = True,
    explicit_reuse: bool = True,
) -> AgentVersion:
    """The next agent version of ``definition``, asked for by environment ``env``, as a
    draft whose preflight is queued.

    The credential set starts from the parent's (for a legacy parent, from ``env``'s own
    refs) and takes the given values and files on top; provider keys go to the
    version's own provider row. A live version with the same identity is returned
    instead of a new one: made ``env``'s default when its check already passed, else
    its check is queued.
    Without ``env`` the version is the connected agent's own: no environment asked
    for it, a parent without a side row contributes only its prompt, and a pass makes
    it the agent's production version, which the environments following production
    then move to (``activate_agent_version``).
    Without ``activate`` nothing is queued or made a default: the caller checks it.
    Without ``explicit_reuse`` (a change found in the background) a reused version
    moves the default as a background activation does, never by hand.
    """
    from simulate.services.harness_versioning.assembly import legacy_runtime
    from simulate.services.harness_versioning.backfill import (
        effective_runtime,
        legacy_source,
        source_access,
    )

    secret_files = list(secret_files or ())
    replaced = {str(name) for name in secret_values or {}} | {
        one.alias for one in secret_files
    }
    reused = None
    with transaction.atomic():
        definition = AgentDefinition.no_workspace_objects.select_for_update().get(
            id=definition.id
        )
        organization = definition.organization
        if parent is not None and parent.agent_definition_id != definition.id:
            raise HostedHarnessError(
                "agent_parent_invalid",
                "the parent version belongs to another agent",
                status_code=422,
            )
        parent_runtime = _side_row(parent.id, blob=True) if parent else None
        if parent is not None and parent_runtime is None and env is not None:
            # Not an empty agent: the environment it is pinned to runs its own.
            if env.agent_version_id != parent.id:
                raise _agent_versions_unavailable()
            parent_runtime = legacy_side_row(env, parent)
        parent_row = _own_provider_row(getattr(parent, "id", None))
        legacy_parent = parent_runtime is not None and legacy_runtime(parent_runtime)
        if legacy_parent and env is None:
            # A legacy row's credentials live in an environment's refs; with no
            # environment to read them from the new version would drop them.
            raise _agent_versions_unavailable()
        # A legacy row is shared: the asking environment's own facts are the parent's.
        parent_facts = effective_runtime(env, parent_runtime)
        replaced_names: set[str] = set()
        if parent_runtime is None:
            secret_set, provider_secrets = CredentialSet(), _row_values(parent_row)
        elif legacy_parent and env is not None:
            secret_set, provider_secrets = _legacy_values(env, organization, replaced)
            replaced_names = replaced & set(
                ((env.payload or {}).get("agent") or {}).get("secret_refs") or {}
            )
        else:
            secret_set = credential_set(parent_runtime)
            provider_secrets = _row_values(parent_row)
        parent_fingerprints = _fingerprints(
            organization.id, secret_set, provider_secrets
        )
        # Not read, but replaced: a change, not an addition.
        parent_fingerprints.update(dict.fromkeys(replaced_names, "unread"))

        base = dict(parent_facts.agent_payload or {}) if parent_facts else {}
        payload = copy.deepcopy(dict(agent_payload) if agent_payload else base)
        if agent_payload and base.get("definition_owner"):
            # Who owns the agent is the agent's, whatever the version's settings.
            payload["definition_owner"] = base["definition_owner"]
        for name in _OWN_PAYLOAD_KEYS:
            payload.pop(name, None)
        connector = str(payload.get("connector") or "")
        _overlay(secret_set, provider_secrets, secret_values, secret_files)
        family = _family(
            provider_secrets, connector, getattr(parent_row, "provider_type", None)
        )
        _keep_one_family(secret_set, provider_secrets, family)
        secret_blob = _encode(secret_set)
        fingerprints = _fingerprints(organization.id, secret_set, provider_secrets)

        parent_kind = getattr(parent_facts, "source_kind", None)
        parent_source = getattr(parent_facts, "source", None) or {}
        if source is not None or parent_runtime is None:
            source_kind, source_facts = legacy_source(
                {"source": dict(source or {}), "agent": payload}
            )
            fingerprint = None
        else:
            source_kind, source_facts = parent_kind, copy.deepcopy(parent_source)
            fingerprint = None if legacy_parent else parent_runtime.source_fingerprint
        # Visibility and installation, outside the identity: a private clone needs
        # them. A row backfilled without them takes the environment's.
        access = (
            source_access({"source": source})
            if source is not None
            else base.get("source_access")
            or source_access((env.payload if env is not None else None) or {})
        )
        payload["source_access"] = copy.deepcopy(access)
        asking = env is not None and activate
        # A version checked by its caller (a rerun's) is never made a default by it.
        payload["requested_by_environments"] = [str(env.id)] if asking else []
        # Each asking environment's default then: a later move of it is not undone.
        payload["requested_defaults"] = (
            {str(env.id): _default_id(env)} if asking else {}
        )
        requested_at = timezone.now()
        payload["requested_at"] = (
            {str(env.id): requested_at.isoformat()} if asking else {}
        )
        if env is not None:
            payload["created_for_environment"] = str(env.id)
        else:
            payload.setdefault("definition_owner", CONNECTED_OWNER)
        # A legacy parent's description may be another environment's prompt.
        description = (
            prompt
            or (
                configured_prompt(env.payload)
                if legacy_parent and env is not None
                else ""
            )
            or (parent.description if parent else "")
            or ""
        )
        prompt_hash = (
            parent_runtime.prompt_hash
            if not prompt and parent_runtime is not None
            else _prompt_hash(description)
        )
        names = secret_set.names()
        runtime = HarnessAgentVersionRuntime(
            agent_definition=definition,
            organization=organization,
            workspace_id=definition.workspace_id,
            prompt_hash=prompt_hash,
            source_kind=source_kind,
            source=source_facts,
            source_fingerprint=fingerprint,
            agent_payload=payload,
            credential_set_hash=hashing.credential_set_hash(fingerprints),
            credential_fingerprints=fingerprints,
            credential_names_hash=hashing.credential_names_hash(names),
            credential_names=names,
            required_variable_names=(
                copy.deepcopy(parent_runtime.required_variable_names)
                if parent_runtime is not None
                else None
            ),
            preflight_status=Status.PENDING,
            check_started_at=requested_at if activate else None,
            parent=parent_runtime,
            trigger=trigger,
            created_by=user,
        )
        runtime.hash = _runtime_hash(runtime)
        # Locked, so a check finishing meanwhile waits and then sees this request.
        existing = (
            HarnessAgentVersionRuntime.no_workspace_objects.filter(
                agent_definition_id=definition.id, hash=runtime.hash
            )
            .exclude(preflight_status__in=_NOT_DEDUPED)
            .select_related("agent_version")
            .select_for_update(of=("self",))
            .defer("credential_blob")
            .first()
        )
        if existing is not None:
            stored = existing.agent_payload or {}
            requested = list(stored.get("requested_by_environments") or [])
            if activate and env is not None:
                restarted = (
                    {"check_started_at": requested_at}
                    if existing.preflight_status == Status.PENDING
                    else {}
                )
                # A version still checked activates for every environment that asked.
                HarnessAgentVersionRuntime.no_workspace_objects.filter(
                    id=existing.id
                ).update(
                    agent_payload={
                        **stored,
                        "requested_by_environments": (
                            requested
                            if str(env.id) in requested
                            else [*requested, str(env.id)]
                        ),
                        "requested_defaults": {
                            **(stored.get("requested_defaults") or {}),
                            str(env.id): _default_id(env),
                        },
                        "requested_at": {
                            **(stored.get("requested_at") or {}),
                            str(env.id): requested_at.isoformat(),
                        },
                    },
                    updated_at=timezone.now(),
                    **restarted,
                )
            if activate and existing.preflight_status == Status.PENDING:
                # Coalesces with a check already running; restarts a lost one.
                transaction.on_commit(
                    lambda: _schedule_preflight(existing.agent_version_id)
                )
            reused = existing
        else:
            diff = (
                diffs.agent_diff(
                    _facts(
                        parent_kind,
                        parent_source,
                        parent_runtime.prompt_hash,
                        parent_fingerprints,
                    ),
                    _facts(source_kind, source_facts, prompt_hash, fingerprints),
                )
                if parent_runtime is not None
                else None
            )
            summary = _summary(diff)
            commit_message = f"{_TRIGGER_LABELS.get(trigger, trigger)}: {summary}"[
                :1024
            ]
            version = AgentVersion.no_workspace_objects.create(
                agent_definition=definition,
                organization=organization,
                workspace=definition.workspace,
                version_number=next_version_number(definition.id),
                description=description,
                commit_message=commit_message,
                status="draft",
            )
            # A legacy parent's keys come from the environment's refs, not its row.
            template = parent_row
            if legacy_parent and getattr(parent_row, "provider_type", None) != family:
                template = None
            _store_provider_credentials(
                version,
                template,
                provider_secrets,
                family,
                keep_keys=not legacy_parent,
            )
            # Read again: the first snapshot cached the version as having no provider
            # row.
            version = AgentVersion.no_workspace_objects.get(id=version.id)
            version.configuration_snapshot = version.create_snapshot(
                commit_message=commit_message
            )
            _own_snapshot_facts(
                version.configuration_snapshot,
                version.description,
                source_kind,
                source_facts,
            )
            version.save(update_fields=["configuration_snapshot", "updated_at"])
            runtime.agent_version = version
            runtime.summary = summary
            runtime.diff = diff
            runtime.credential_blob = secret_blob
            try:
                with transaction.atomic():
                    runtime.save()
            except IntegrityError:
                # A retry made a version with this identity live meanwhile.
                raise _duplicate(_live_duplicate(runtime)) from None
            # TODO: one draft per definition: discard the previous pending or failed
            # draft.
            if activate:
                transaction.on_commit(lambda: _schedule_preflight(version.id))
    if reused is not None:
        logger.info(
            "harness_agent_version_reused version=%s definition=%s trigger=%s",
            reused.agent_version_id,
            definition.id,
            trigger,
        )
        if reused.preflight_status == Status.PASSED and activate:
            # Asked for by a person, a newer default does not stand in its way; found
            # in the background, it moves only what a background activation would.
            try:
                if env is None:
                    activate_agent_version(reused.agent_version)
                else:
                    _activate(
                        reused.agent_version,
                        [env.id],
                        explicit=explicit_reuse,
                        user=user,
                    )
            except Exception as exc:  # noqa: BLE001 - recorded; a default move retries
                logger.warning(
                    "harness_agent_version_activation_failed version=%s"
                    " environment=%s error=%s",
                    reused.agent_version_id,
                    getattr(env, "id", None),
                    type(exc).__name__,
                )
        return reused.agent_version
    logger.info(
        "harness_agent_version_created version=%s definition=%s trigger=%s parent=%s"
        " names=%d",
        version.id,
        definition.id,
        trigger,
        getattr(parent, "id", None),
        len(names),
    )
    return version


def _agent_versions_unavailable() -> HostedHarnessError:
    return HostedHarnessError(
        "agent_versions_unavailable",
        "This environment's agent has no versions to change the credentials of",
        status_code=422,
    )


def staged_credential_files(
    organization: Any, refs: Iterable[Mapping[str, Any]]
) -> list[CredentialFile]:
    """The files staged through ``secret-files/``, read from their stored secrets; the
    staged rows are kept."""
    from simulate.services.harness_credentials import HOSTED_FILE_KEY_PREFIX
    from simulate.services.hosted_harness_gateway import (
        TARGET_GOOGLE_ADC_ALIAS,
        resolve_secret_reference,
    )

    secret_files = []
    for ref in refs:
        if not str(ref.get("key") or "").startswith(HOSTED_FILE_KEY_PREFIX):
            raise HostedHarnessError(
                "credential_file_invalid",
                "only a file staged through secret-files can be added",
                status_code=422,
            )
        secret_value = resolve_secret_reference(
            organization, TARGET_GOOGLE_ADC_ALIAS, ref
        )
        secret_files.append(
            CredentialFile(
                alias=TARGET_GOOGLE_ADC_ALIAS, content=secret_value.encode("utf-8")
            )
        )
    return secret_files


def _outstanding_request(
    environment: HostedHarnessJob, default: AgentVersion
) -> AgentVersion | None:
    """The environment's latest request when still on its way to being the default:
    pending, or passed and not yet activated here. A change builds on it, so neither
    undoes the other."""
    from simulate.services.harness_versioning.read import _activation_outcome

    environment_id = str(environment.id)
    latest = max(
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_definition_id=default.agent_definition_id,
            agent_payload__requested_by_environments__contains=[environment_id],
        )
        .select_related("agent_version__agent_definition")
        .defer("credential_blob"),
        key=lambda row: str(
            ((row.agent_payload or {}).get("requested_at") or {}).get(environment_id)
            or ""
        ),
        default=None,
    )
    if latest is None or latest.agent_version_id == default.id:
        return None
    if _turned_down(environment, latest.agent_version, latest):
        # Moved by hand or passed over since: build on the default instead.
        return None
    if latest.preflight_status == Status.PENDING or (
        latest.preflight_status == Status.PASSED
        and _activation_outcome(latest.preflight_report, environment_id)
        in (None, "environment_busy", "failed")
    ):
        return latest.agent_version
    return None


def change_credentials(
    environment: HostedHarnessJob,
    *,
    secret_values: Mapping[str, str],
    secret_files: Iterable[CredentialFile] = (),
    user: Any = None,
    parent: AgentVersion | None = None,
    activate: bool = True,
) -> AgentVersion:
    """The next agent version of ``parent`` (when not given, the environment's request
    still on its way to being the default, else the default) with these values and
    files over its credential set."""
    from simulate.services.harness_versioning.versions import versioning_enabled

    if not versioning_enabled() or environment.environment_id is not None:
        raise _agent_versions_unavailable()
    if parent is None:
        parent = (
            AgentVersion.no_workspace_objects.select_related("agent_definition")
            .filter(
                id=environment.agent_version_id,
                organization_id=environment.organization_id,
            )
            .first()
            if environment.agent_version_id
            else None
        )
        if parent is not None and activate:
            parent = _outstanding_request(environment, parent) or parent
    if parent is None:
        raise _agent_versions_unavailable()
    secret_files = list(secret_files)
    names = [*secret_values, *(one.alias for one in secret_files)]
    # A rerun's caller checks against the rerun's own pair.
    if activate and not changed_set_runnable(environment, environment, parent, names):
        raise HostedHarnessError(
            "version_not_runnable",
            "These variables can't be used yet: this environment would have to be"
            " prepared again with them, and that's turned off",
            status_code=422,
        )
    return create_agent_version(
        parent.agent_definition,
        parent=parent,
        env=environment,
        secret_values=secret_values,
        secret_files=secret_files,
        trigger=Trigger.CREDENTIALS,
        user=user,
        activate=activate,
    )


def _schedule_preflight(version_id: Any) -> None:
    from simulate.tasks.harness_agent_versions import schedule_agent_preflight

    try:
        schedule_agent_preflight(version_id)
    except Exception:  # noqa: BLE001 - a retry starts it again
        logger.exception("could not start the agent preflight version=%s", version_id)


# --- preflight ----------------------------------------------------------------------


def _payload_source(runtime: HarnessAgentVersionRuntime) -> dict[str, Any]:
    source = dict(runtime.source or {})
    access = (runtime.agent_payload or {}).get("source_access") or {}
    kind = runtime.source_kind
    if access.get("kind") == "remote" or source.get("provider") == "remote":
        # A remote target owns its credentials: nothing to scan or probe.
        return {"kind": "remote"}
    if kind in (
        HarnessAgentVersionRuntime.SourceKind.GITHUB,
        HarnessAgentVersionRuntime.SourceKind.ARCHIVE,
    ):
        reach = {
            name: access[name]
            for name in ("visibility", "installation_id")
            if access.get(name)
        }
        return {"kind": str(kind), **source, **reach}
    # Provider, phone and LiveKit agents have no source tree to read.
    return {"kind": "provider"}


def _preflight_payload(
    runtime: HarnessAgentVersionRuntime,
    secret_set: CredentialSet,
    provider_secrets: Mapping[str, str],
) -> dict[str, Any]:
    agent = runtime.agent_payload or {}
    names = sorted({*secret_set.names(), *provider_secrets})
    return {
        "source": _payload_source(runtime),
        "agent": {
            "connector": str(agent.get("connector") or "auto"),
            "mode": agent.get("mode"),
            "call_direction": agent.get("call_direction"),
            "config": copy.deepcopy(agent.get("config") or {}),
            "secret_refs": {name: agent_version_secret_ref(name) for name in names},
        },
        "credential_values": {**secret_set.env, **provider_secrets},
    }


def _report(
    checks: list[dict[str, Any]],
    missing: Iterable[str],
    probe: Iterable[Mapping[str, Any]],
    error: str | None,
    scan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Names and statuses only: check details and probe messages are not stored."""
    return {
        # What the source scan found, by name, so a credentials-only child reuses it.
        **({"source_scan": dict(scan)} if scan is not None else {}),
        "checks": [
            {
                "id": check["id"],
                "label": check["label"],
                "status": check["status"],
                "missing": [str(name) for name in check.get("missing") or []],
            }
            for check in checks
        ],
        "missing": sorted({str(name) for name in missing}),
        "invalid": sorted(
            {
                str(alias)
                for item in probe
                if not item.get("ok")
                for alias in item.get("aliases") or []
            }
        ),
        "error": error,
    }


def _run_preflight(
    organization: Any,
    secret_payload: dict[str, Any],
    scan: Mapping[str, Any] | None = None,
) -> tuple[bool, dict]:
    """Check the source and the credentials; a ``scan`` already made of this source
    stands for acquiring it again."""
    from simulate.services.harness_provider import run_preflight_checks

    analysis = (
        (
            list(scan.get("detected_connectors") or []),
            list(scan.get("required_files") or []),
            int(scan.get("scanned_files") or 0),
        )
        if scan is not None
        else None
    )
    secret_checks, secret_readiness, secret_probe, source_error, analysis = (
        run_preflight_checks(organization, secret_payload, analysis)
    )
    detected, required_files, scanned = analysis
    passed = not any(check["status"] == "failed" for check in secret_checks)
    error = source_error.code if source_error is not None else None
    found = (
        {
            "detected_connectors": sorted(str(name) for name in detected),
            "required_files": sorted(str(name) for name in required_files),
            "scanned_files": int(scanned or 0),
        }
        if source_error is None
        else None
    )
    return passed, _report(
        secret_checks, secret_readiness["missing"], secret_probe, error, found
    )


def _parent_scan(runtime: HarnessAgentVersionRuntime) -> Mapping[str, Any] | None:
    """The parent's source scan, when a credentials-only version has its source."""
    if runtime.trigger != Trigger.CREDENTIALS or runtime.parent_id is None:
        return None
    parent = (
        HarnessAgentVersionRuntime.no_workspace_objects.filter(id=runtime.parent_id)
        .defer("credential_blob")
        .first()
    )
    if parent is None or _payload_source(parent) != _payload_source(runtime):
        return None
    scan = (parent.preflight_report or {}).get("source_scan")
    return scan if isinstance(scan, Mapping) else None


def preflight_agent_version(
    version: AgentVersion, *, activate: bool = True
) -> PreflightResult:
    """Check a pending version's source and credentials; a pass activates it unless
    ``activate`` is off. A version that already passed is activated again, so a retry
    after a partial activation moves the environments still left.

    The payload is rebuilt from the side row with the values in memory only.
    """
    runtime = (
        HarnessAgentVersionRuntime.no_workspace_objects.select_related("organization")
        .filter(agent_version_id=version.id)
        .first()
    )
    if runtime is None:
        return PreflightResult("missing", None)
    if runtime.preflight_status == Status.PASSED and activate:
        activate_agent_version(version)
        current = _side_row(version.id)
        return PreflightResult(
            getattr(current, "preflight_status", "missing"),
            getattr(current, "preflight_report", None),
        )
    if runtime.preflight_status != Status.PENDING:
        return PreflightResult(runtime.preflight_status, runtime.preflight_report)
    try:
        secret_set = credential_set(runtime)
        provider_secrets = _row_values(_own_provider_row(version.id))
    except HostedHarnessError as exc:
        passed, report = False, _report([], [], [], exc.code)
    except Exception:  # noqa: BLE001 - nothing of a value may reach the report
        passed, report = False, _report([], [], [], "credential_unavailable")
    else:
        try:
            passed, report = _run_preflight(
                runtime.organization,
                _preflight_payload(runtime, secret_set, provider_secrets),
                _parent_scan(runtime),
            )
        except Exception as exc:  # noqa: BLE001 - its text may carry a value
            logger.warning(
                "harness_agent_version_preflight_error version=%s error=%s",
                version.id,
                type(exc).__name__,
            )
            passed, report = False, _report([], [], [], "preflight_error")
    status = Status.PASSED if passed else Status.FAILED
    now = timezone.now()
    # A version discarded while it was checked stays discarded.
    updated = HarnessAgentVersionRuntime.no_workspace_objects.filter(
        id=runtime.id, preflight_status=Status.PENDING
    ).update(
        preflight_status=status,
        preflight_report=report,
        preflighted_at=now,
        updated_at=now,
    )
    if not updated:
        current = _side_row(version.id)
        if activate and getattr(current, "preflight_status", None) == Status.PASSED:
            # Another check passed it first; this one still moves who asked.
            activate_agent_version(version)
            current = _side_row(version.id)
        return PreflightResult(
            getattr(current, "preflight_status", "missing"),
            getattr(current, "preflight_report", None),
        )
    logger.info(
        "harness_agent_version_preflighted version=%s status=%s", version.id, status
    )
    payload = runtime.agent_payload or {}
    if passed and activate:
        activate_agent_version(version)
        report = _side_row(version.id).preflight_report
    elif passed and payload.get("requested_by_environments"):
        # A rerun's check passed a version others asked for: their activation runs.
        transaction.on_commit(lambda: _schedule_preflight(version.id))
    elif passed and payload.get("created_for_environment"):
        # A rerun's version: recorded as moving no default, so nothing waits on it.
        _record_activation(
            version.id, {str(payload["created_for_environment"]): "rerun_only"}
        )
        report = _side_row(version.id).preflight_report
    return PreflightResult(status, report)


def requested_here(
    requested_by: Iterable[Any] | None, created_for: Any, environment_id: Any
) -> bool:
    """Whether the environment asked for a version: its change, or its rerun's."""
    environment_id = str(environment_id)
    if created_for and str(created_for) == environment_id:
        return True
    return environment_id in {str(one) for one in requested_by or []}


def _default_lineage(environment: HostedHarnessJob) -> set[Any]:
    """The environment's default agent version and the versions it descends from,
    up to one another environment made and this one never asked for: its ancestors
    are that environment's."""
    lineage: set[Any] = set()
    rows = HarnessAgentVersionRuntime.no_workspace_objects.filter(
        agent_version_id=environment.agent_version_id
    )
    while environment.agent_version_id:
        found = rows.values_list(
            "parent_id",
            "agent_version_id",
            "agent_payload__created_for_environment",
            "agent_payload__requested_by_environments",
        ).first()
        if found is None or found[1] in lineage:
            break
        lineage.add(found[1])
        if not found[0] or (
            found[2] and not requested_here(found[3], found[2], environment.id)
        ):
            break
        rows = HarnessAgentVersionRuntime.no_workspace_objects.filter(id=found[0])
    if environment.agent_version_id:
        lineage.add(environment.agent_version_id)
    return lineage


def agent_pick_allowed(
    environment: HostedHarnessJob,
    version_id: Any,
    runtime: HarnessAgentVersionRuntime | None,
    lineage: set[Any] | None = None,
) -> bool:
    """Whether a run may pick a version of the environment's agent, or a move make it
    the default: one whose check passed, or a legacy or pre-versioning one of the
    default's ``lineage``, whose facts are the environment's own payload."""
    status = runtime.preflight_status if runtime is not None else None
    if status == Status.PASSED:
        return True
    if status not in (None, Status.LEGACY):
        return False
    if lineage is None:
        lineage = _default_lineage(environment)
    return str(version_id) in {str(one) for one in lineage}


def require_agent_pick(
    environment: HostedHarnessJob, version_id: Any, *, refusal: str, lock: bool = False
) -> None:
    """Refuse a version a run may not pick or a move may not make the default."""
    from simulate.services.hosted_harness import _not_pickable, agent_version_pickable

    runtime = _side_row(version_id, lock=lock)
    if not agent_version_pickable(environment, version_id):
        raise _not_pickable("agent")
    if not agent_pick_allowed(environment, version_id, runtime):
        # TODO: run a legacy agent version from the payload it was recorded with;
        # until then it would run the current payload under its older label.
        raise HostedHarnessError(
            "version_not_pickable",
            refusal,
            status_code=422,
            details={"piece": "agent"},
        )


def require_environments_version(
    environment: HostedHarnessJob, version: AgentVersion
) -> None:
    """Refuse acting on a version another environment's change made; one with no
    check record is left to the action's own refusal."""
    found = (
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=version.id
        )
        .values_list(
            "agent_payload__requested_by_environments",
            "agent_payload__created_for_environment",
        )
        .first()
    )
    if found is None or requested_here(*found, environment.id):
        return
    if version.id in _default_lineage(environment):
        return
    raise HostedHarnessError(
        "version_not_yours",
        "This version belongs to another environment's change",
        status_code=422,
        details={"piece": "agent"},
    )


def retry_preflight(version: AgentVersion) -> None:
    """Check a failed (or stuck pending) version again."""
    with transaction.atomic():
        # The definition first, as a new version takes it: a change made meanwhile
        # then sees this row live and is it, instead of repeating its identity.
        AgentDefinition.no_workspace_objects.select_for_update().filter(
            id=version.agent_definition_id
        ).first()
        runtime = _side_row(version.id, lock=True)
        if runtime is None or runtime.preflight_status not in _RETRYABLE:
            raise HostedHarnessError(
                "version_not_retryable",
                "Only a version whose check failed can be checked again",
                status_code=422,
                details={"piece": "agent"},
            )
        # A stuck check is dated from its restart, not from when it was first asked.
        runtime.check_started_at = timezone.now()
        if runtime.preflight_status == Status.FAILED:
            runtime.preflight_status = Status.PENDING
            duplicate = _live_duplicate(runtime)
            if duplicate is not None:
                raise _duplicate(duplicate)
        try:
            with transaction.atomic():
                runtime.save(
                    update_fields=["preflight_status", "check_started_at", "updated_at"]
                )
        except IntegrityError:
            # Another version with this identity went live meanwhile.
            raise _duplicate(_live_duplicate(runtime)) from None
        transaction.on_commit(lambda: _schedule_preflight(version.id))
    logger.info("harness_agent_version_retried version=%s", version.id)


def _live_duplicate(
    runtime: HarnessAgentVersionRuntime,
) -> HarnessAgentVersionRuntime | None:
    """A live version with ``runtime``'s identity, which a pending row would repeat."""
    return (
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_definition_id=runtime.agent_definition_id, hash=runtime.hash
        )
        .exclude(id=runtime.id)
        .exclude(preflight_status__in=(*_NOT_DEDUPED, Status.LEGACY))
        .select_related("agent_version")
        .defer("credential_blob")
        .first()
    )


def _duplicate(existing: HarnessAgentVersionRuntime | None) -> HostedHarnessError:
    from simulate.services.harness_versioning.read import _agent_label, _message_label

    version = getattr(existing, "agent_version", None)
    label = _message_label(
        _agent_label(
            getattr(version, "version_name", None),
            getattr(version, "version_number", None),
        )
    )
    return HostedHarnessError(
        "agent_version_duplicate",
        f"This version is the same as {label}; use {label} instead",
        status_code=409,
        details={
            "piece": "agent",
            "agent_version_id": str(version.id) if version is not None else None,
        },
    )


# --- activation ---------------------------------------------------------------------


def _busy(environment: HostedHarnessJob) -> bool:
    return environment.state != HostedHarnessJob.State.COMPLETED or bool(
        ((environment.payload or {}).get("metadata") or {}).get("scenario_extend")
    )


def _requested_defaults(version_id: Any) -> Mapping[str, Any]:
    """Each asking environment's default when it asked for this version."""
    return (
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=version_id
        )
        .values_list("agent_payload__requested_defaults", flat=True)
        .first()
        or {}
    )


def _requested_at(version_id: Any) -> Mapping[str, Any]:
    """When each asking environment last asked for this version."""
    return (
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=version_id
        )
        .values_list("agent_payload__requested_at", flat=True)
        .first()
        or {}
    )


def _moved_by_hand_since(environment: HostedHarnessJob, asked_at: Any) -> bool:
    """Whether the environment's default was moved by hand after ``asked_at``."""
    moved = ((environment.payload or {}).get("metadata") or {}).get(HAND_MOVE_KEY) or {}
    moved_at = parse_datetime(str(moved.get("at") or ""))
    asked = parse_datetime(str(asked_at or ""))
    return moved_at is not None and asked is not None and moved_at > asked


def hand_move(user: Any, version_id: Any) -> dict[str, Any]:
    """A move of the default an earlier request's activation never undoes."""
    return {
        "at": timezone.now().isoformat(),
        "by": str(user.id) if getattr(user, "id", None) else None,
        "to": str(version_id),
    }


def record_hand_move(locked: HostedHarnessJob, user: Any, version_id: Any) -> None:
    """Record on the locked environment that ``version_id`` is its default by hand."""
    payload = dict(locked.payload or {})
    payload["metadata"] = {
        **(payload.get("metadata") or {}),
        HAND_MOVE_KEY: hand_move(user, version_id),
    }
    locked.payload = payload
    locked.save(update_fields=["payload", "updated_at"])


def _turned_down(
    locked: HostedHarnessJob,
    version: AgentVersion,
    runtime: HarnessAgentVersionRuntime,
) -> str | None:
    """Why a background activation leaves the environment's default where it is."""
    environment_id = str(locked.id)
    payload = runtime.agent_payload or {}
    asked_at = (payload.get("requested_at") or {}).get(environment_id)
    if _moved_by_hand_since(locked, asked_at):
        # Moved by hand after this was asked for, even back to a version it asked
        # for earlier: that move stands.
        return "default_moved_by_hand"
    current = (
        AgentVersion._base_manager.filter(id=locked.agent_version_id).first()
        if locked.agent_version_id
        else None
    )
    if current is not None and current.agent_definition_id == (
        version.agent_definition_id
    ):
        # The environment's later request stands; by number when either has no time.
        asked = parse_datetime(str(asked_at or ""))
        current_asked = parse_datetime(
            str(_requested_at(locked.agent_version_id).get(environment_id) or "")
        )
        if (
            current_asked > asked
            if asked is not None and current_asked is not None
            else (current.version_number or 0) > (version.version_number or 0)
        ):
            return "superseded"
    recorded = payload.get("requested_defaults") or {}
    if (
        environment_id in recorded
        and recorded[environment_id] != _default_id(locked)
        and _requested_defaults(locked.agent_version_id).get(environment_id)
        != recorded[environment_id]
    ):
        # Moved meanwhile, and not by a request of its own from the same default.
        return "not_parent_default"
    return None


def _activate_on(
    environment_id: Any,
    version: AgentVersion,
    *,
    explicit: bool = False,
    user: Any = None,
) -> str:
    """Move one environment that asked for ``version`` to it, unless a newer version
    of the agent is already its default or the default was moved since; an
    ``explicit`` request moves it from any default."""
    from simulate.services.harness_versioning.nudges import schedule_environment_nudge
    from simulate.services.harness_versioning.versions import move_agent
    from simulate.services.hosted_harness import agent_version_pickable

    with transaction.atomic():
        locked = (
            HostedHarnessJob.no_workspace_objects.select_for_update()
            .filter(id=environment_id, environment__isnull=True)
            .first()
        )
        if locked is None:
            return "environment_gone"
        if locked.agent_version_id == version.id:
            if explicit:
                # Asked for again by hand: an earlier request never replaces it.
                record_hand_move(locked, user, version.id)
            return "already_default"
        # Locked, so a discard waits until this move is visible to its in-use check.
        runtime = _side_row(version.id, lock=True)
        if runtime is None or runtime.preflight_status != Status.PASSED:
            return "discarded"
        if not agent_version_pickable(locked, version.id):
            return "not_bound"
        if not explicit:
            reason = _turned_down(locked, version, runtime)
            if reason is not None:
                return reason
        if _busy(locked):
            # Moved by rebuild.retry_deferred_activations once it finishes.
            return "environment_busy"
        outgoing = locked.agent_version_id
        try:
            with transaction.atomic():
                # Asked for again by hand: an earlier request never undoes it.
                move_agent(
                    locked,
                    version,
                    moved=hand_move(user, version.id) if explicit else None,
                )
        except HostedHarnessError as exc:
            return exc.code
        schedule_environment_nudge(
            locked.id,
            version.id,
            default_moved=True,
            outgoing_agent_version_id=outgoing,
        )
    return "activated"


def _record_activation(version_id: Any, outcomes: Mapping[str, str]) -> None:
    """Each environment's latest outcome, by id and code, in the preflight report."""
    if not outcomes:
        return
    with transaction.atomic():
        runtime = _side_row(version_id, lock=True)
        if runtime is None:
            return
        report = dict(runtime.preflight_report or {})
        kept = [
            entry
            for entry in report.get("activation") or []
            if entry.get("environment_id") not in outcomes
        ]
        # The same outcome again is counted: a refusal that never clears stops.
        repeated = {
            entry.get("environment_id"): int(entry.get("attempts") or 1) + 1
            for entry in report.get("activation") or []
            if outcomes.get(entry.get("environment_id")) == entry.get("outcome")
        }
        report["activation"] = kept + [
            {"environment_id": environment_id, "outcome": outcome}
            | (
                {"attempts": repeated[environment_id]}
                if environment_id in repeated
                else {}
            )
            for environment_id, outcome in outcomes.items()
        ]
        HarnessAgentVersionRuntime.no_workspace_objects.filter(id=runtime.id).update(
            preflight_report=report, updated_at=timezone.now()
        )


def _activate(
    version: AgentVersion,
    environment_ids: Iterable[Any],
    *,
    explicit: bool = False,
    user: Any = None,
) -> list[Any]:
    from simulate.services.harness_versioning.nudges import schedule_environment_nudge

    moved = []
    outcomes: dict[str, str] = {}
    errors: list[Exception] = []
    for environment_id in environment_ids:
        try:
            outcome = _activate_on(
                environment_id, version, explicit=explicit, user=user
            )
        except Exception as exc:  # noqa: BLE001 - never blocks the others
            logger.warning(
                "harness_agent_version_activation_failed version=%s environment=%s"
                " error=%s",
                version.id,
                environment_id,
                type(exc).__name__,
            )
            errors.append(exc)
            outcome = "failed"
        outcomes[str(environment_id)] = outcome
        if outcome == "activated":
            moved.append(environment_id)
            logger.info(
                "harness_agent_version_activated version=%s environment=%s",
                version.id,
                environment_id,
            )
        elif outcome != "already_default":
            logger.info(
                "harness_agent_version_not_activated version=%s environment=%s"
                " reason=%s",
                version.id,
                environment_id,
                outcome,
            )
        if outcome in _OFFERED:
            schedule_environment_nudge(environment_id, version.id, default_moved=False)
    _record_activation(version.id, outcomes)
    if errors:
        # A transient failure: the activity runs again and moves what is left.
        raise errors[0]
    return moved


def activate_agent_version(version: AgentVersion) -> list[Any]:
    """Make a passed version the default of the environments that asked for it.

    Any other environment whose default is its parent (one with a credential set of
    its own) is recorded as not having asked and stays where it is. An environment is
    skipped when the version was discarded meanwhile, a newer version is already its
    default, or the version is not one it can pick. A rerun's version, asked for by
    none, moves no environment. Returns the environments moved.
    """
    from simulate.services.harness_versioning.assembly import legacy_runtime
    from simulate.services.harness_versioning.nudges import schedule_environment_nudge

    runtime = (
        HarnessAgentVersionRuntime.no_workspace_objects.select_related("parent")
        .defer("credential_blob")
        .filter(agent_version_id=version.id)
        .first()
    )
    if runtime is None:
        return []
    parent = runtime.parent
    parent_version_id = parent.agent_version_id if parent is not None else None
    payload = runtime.agent_payload or {}
    requested = [
        str(environment_id)
        for environment_id in payload.get("requested_by_environments") or []
    ]
    if (
        not requested
        and not payload.get("created_for_environment")
        and payload.get("definition_owner") == CONNECTED_OWNER
    ):
        return _activate_production(version, runtime)
    others = []
    # TODO: offer legacy sharers a version of their own (Q6).
    if parent is not None and not legacy_runtime(parent):
        others = [
            str(environment_id)
            for environment_id in HostedHarnessJob.no_workspace_objects.filter(
                organization_id=version.organization_id,
                environment__isnull=True,
                agent_version_id=parent_version_id,
            )
            .order_by("created_at")
            .values_list("id", flat=True)
            if str(environment_id) not in requested
        ]
    if not requested and payload.get("created_for_environment"):
        # A rerun's version: picked explicitly, never made a default.
        _record_activation(
            version.id,
            dict.fromkeys(
                [str(payload["created_for_environment"]), *others], "rerun_only"
            ),
        )
        return []
    _record_activation(version.id, dict.fromkeys(others, "not_requested"))
    for environment_id in others:
        schedule_environment_nudge(environment_id, version.id, default_moved=False)
    return _activate(version, requested)


def _recent(at: Any) -> bool:
    return at is not None and timezone.now() - at < _WAIT_LIMIT


def _still_waiting(runtime: HarnessAgentVersionRuntime, by_environment: Any) -> bool:
    """Whether another environment asked for this version and can still be moved to
    it: its check or activation is still running, or it was busy and still is."""
    from simulate.services.harness_versioning.versions import UNREADY_STATES

    if runtime.preflight_status not in (Status.PENDING, Status.PASSED):
        return False
    outcomes = {
        entry.get("environment_id"): entry.get("outcome")
        for entry in (runtime.preflight_report or {}).get("activation") or []
        if isinstance(entry, Mapping)
    }
    version = runtime.agent_version
    for environment_id in (runtime.agent_payload or {}).get(
        "requested_by_environments"
    ) or []:
        outcome = outcomes.get(str(environment_id))
        if str(environment_id) == str(by_environment) or outcome not in (
            None,
            "environment_busy",
            "failed",
        ):
            continue
        environment = HostedHarnessJob.no_workspace_objects.filter(
            id=environment_id, environment__isnull=True
        ).first()
        if (
            environment is None
            or environment.agent_version_id == version.id
            or _turned_down(environment, version, runtime) is not None
        ):
            continue
        if outcome == "environment_busy":
            # Moved by rebuild.retry_deferred_activations once it finishes.
            # A failed or canceled build is not finishing: nothing will move it.
            waiting = environment.state not in UNREADY_STATES and _busy(environment)
        elif runtime.preflight_status == Status.PENDING:
            waiting = _recent(runtime.check_started_at or runtime.created_at)
        else:
            # The activity retries a failed or unfinished activation for a while.
            waiting = _recent(runtime.preflighted_at)
        if waiting:
            return True
    return False


def _version_shared(message: str) -> HostedHarnessError:
    return HostedHarnessError(
        "version_shared",
        message,
        status_code=422,
        details={"piece": "agent"},
    )


def _held_elsewhere(
    version: AgentVersion, created_for: Any, by_environment: Any
) -> bool:
    """Whether another environment made this version or its default descends from
    it."""
    if (
        created_for
        and str(created_for) != str(by_environment)
        and HostedHarnessJob.no_workspace_objects.filter(
            id=created_for, environment__isnull=True
        ).exists()
    ):
        return True
    others = (
        HostedHarnessJob.no_workspace_objects.filter(
            organization_id=version.organization_id,
            environment__isnull=True,
            agent_version__agent_definition_id=version.agent_definition_id,
        )
        .exclude(id=by_environment)
        .only("id", "agent_version_id")
    )
    return any(version.id in _default_lineage(other) for other in others)


def _discarded_by(runtime: HarnessAgentVersionRuntime, by_environment: Any) -> bool:
    """Record the environment's decision on this head; others may still ask for it.
    Returns whether the payload changed; the caller saves."""
    payload = runtime.agent_payload or {}
    by = list(payload.get("discarded_by") or [])
    if by_environment is None or str(by_environment) in by:
        return False
    runtime.agent_payload = {**payload, "discarded_by": [*by, str(by_environment)]}
    return True


def discard_agent_version(version: AgentVersion, *, by_environment: Any = None) -> None:
    """Set a version aside; it stays stored and numbered, never soft-deleted. A legacy
    version, shared by every environment on it, is kept; so is one another environment
    made, descends from or is still waiting for."""
    from simulate.services.harness_versioning.assembly import legacy_runtime

    with transaction.atomic():
        runtime = _side_row(version.id, lock=True)
        if runtime is None:
            raise HostedHarnessError(
                "version_not_discardable",
                "This agent version can't be discarded",
                status_code=422,
                details={"piece": "agent"},
            )
        if runtime.preflight_status == Status.DISCARDED:
            if _discarded_by(runtime, by_environment):
                runtime.save(update_fields=["agent_payload", "updated_at"])
            return
        if legacy_runtime(runtime):
            raise _version_shared(
                "This older agent version is shared by every environment on this"
                " agent; it can't be discarded"
            )
        if by_environment is not None and _still_waiting(runtime, by_environment):
            raise HostedHarnessError(
                "version_requested_elsewhere",
                "Another environment is still waiting for this version; it can't be"
                " discarded",
                status_code=422,
                details={"piece": "agent"},
            )
        if HostedHarnessJob.no_workspace_objects.filter(
            environment__isnull=True, agent_version_id=version.id
        ).exists():
            raise HostedHarnessError(
                "agent_version_in_use",
                "This agent version is an environment's default; make another one"
                " the default first",
                status_code=422,
                details={"piece": "agent"},
            )
        if by_environment is not None and _held_elsewhere(
            version,
            (runtime.agent_payload or {}).get("created_for_environment"),
            by_environment,
        ):
            raise _version_shared(
                "Another environment made this version or was changed from it; it"
                " can't be discarded"
            )
        runtime.preflight_status = Status.DISCARDED
        _discarded_by(runtime, by_environment)
        runtime.save(update_fields=["preflight_status", "agent_payload", "updated_at"])
        if environment_owned(version.agent_definition_id):
            AgentVersion._base_manager.filter(id=version.id).update(
                status=AgentVersion.StatusChoices.DEPRECATED, updated_at=timezone.now()
            )
    logger.info("harness_agent_version_discarded version=%s", version.id)


# --- agent pin ----------------------------------------------------------------------


def agent_pin_candidate(locked: HostedHarnessJob) -> AgentVersion | None:
    """The version ``fill_agent_pin`` would pin an unpinned environment to, or None."""
    from simulate.services.harness_versioning.backfill import (
        agent_candidate,
        default_models,
    )
    from simulate.services.harness_versioning.versions import versioning_enabled

    if locked.environment_id is not None or locked.agent_version_id is not None:
        return None
    if not versioning_enabled() and locked.environment_version_id is None:
        return None
    # A candidate holding another environment's credential set is passed over.
    version, _ = agent_candidate(default_models(), locked)
    return version


def fill_agent_pin(locked: HostedHarnessJob) -> bool:
    """Pin an environment built without one to its agent version, with a legacy side
    row like the backfill's. Call under the environment's lock; the caller saves.

    Only a version without a side row or with a legacy one is pinned: any other holds
    another environment's credential set, so the newest such version is pinned
    instead, and without one the environment stays unpinned. A legacy row may be
    another environment's too; each reads its own facts from its payload.
    """
    version = agent_pin_candidate(locked)
    if version is None:
        return False
    legacy_side_row(locked, version, Trigger.CREATION)
    locked.agent_version_id = version.id
    logger.info(
        "harness_agent_pin_filled environment=%s agent_version=%s",
        locked.id,
        version.id,
    )
    return True


# --- environment creation -----------------------------------------------------------

# Provider agents one assistant stands for: environments on it share one agent.
_SHARED_CONNECTORS = ("vapi", "retell", "retell_chat")


@dataclass(frozen=True)
class SubmittedCredentials:
    """A creation's credentials, resolved; values never reach a repr."""

    secret_set: CredentialSet
    provider_secrets: dict[str, str] = field(repr=False)


@dataclass(frozen=True)
class CreationCheck:
    """The creation's check of them, by name only."""

    report: dict[str, Any]
    required_variable_names: list[str]


def environment_owned(definition_id: Any) -> bool:
    """Whether an agent was made by and for environments, rather than connected."""
    # TODO: refuse Agents-page and MCP edits with managed_by_environment (step 11).
    return HarnessAgentVersionRuntime._base_manager.filter(
        agent_definition_id=definition_id,
        agent_payload__definition_owner="environment",
    ).exists()


def connected_agent(definition_id: Any) -> bool:
    """Whether an agent was connected on the Agents page, which alone makes its
    versions and moves its production one."""
    return HarnessAgentVersionRuntime._base_manager.filter(
        agent_definition_id=definition_id,
        agent_payload__definition_owner=CONNECTED_OWNER,
    ).exists()


def agent_definition_owner(job: HostedHarnessJob) -> str:
    """``environment`` for an agent made with the environment, else ``legacy``."""
    metadata = (job.payload or {}).get("metadata") or {}
    return str(metadata.get("agent_definition_owner") or "legacy")


CONNECTED_OWNER = "connected"
POLICY_KEY = "agent_policy"
POLICY_FOLLOW = "follow"
POLICY_PINNED = "pinned"


def bound_environments(definition_id: Any) -> list[HostedHarnessJob]:
    """The live environments that test this agent, oldest first."""
    return list(
        HostedHarnessJob.no_workspace_objects.filter(
            run_test__agent_definition_id=definition_id,
            run_test__deleted=False,
            environment__isnull=True,
            deleted=False,
        ).order_by("created_at")
    )


def environment_policy(environment: HostedHarnessJob) -> str:
    """Whether the environment follows the agent's production version or is pinned.
    Only one that chose to follow does; any other keeps its version and is offered
    a new one, so a production change never moves an environment that didn't ask."""
    policy = ((environment.payload or {}).get("metadata") or {}).get(POLICY_KEY)
    return POLICY_FOLLOW if policy == POLICY_FOLLOW else POLICY_PINNED


def make_production(version: AgentVersion) -> None:
    """Make a connected agent's version its production one: the active version the
    Agents page shows and the one following environments move to."""
    Statuses = AgentVersion.StatusChoices
    with transaction.atomic():
        advisory_xact_lock(f"harness-agent-mirror:{version.agent_definition_id}")
        now = timezone.now()
        AgentVersion._base_manager.filter(
            agent_definition_id=version.agent_definition_id, status=Statuses.ACTIVE
        ).exclude(id=version.id).update(status=Statuses.ARCHIVED, updated_at=now)
        AgentVersion._base_manager.filter(id=version.id).update(
            status=Statuses.ACTIVE, updated_at=now
        )


def _activate_production(
    version: AgentVersion, runtime: HarnessAgentVersionRuntime
) -> list[Any]:
    """A connected agent's passed version becomes production; environments following
    production are moved to it as if they had asked, pinned ones are offered it."""
    from simulate.services.agent_compatibility import (
        CLEAN,
        UNMEASURED,
        environment_compatibility,
        stored_document,
    )
    from simulate.services.harness_versioning.nudges import schedule_environment_nudge

    make_production(version)
    document = stored_document(version)
    following, pinned, offered = [], [], {}
    for environment in bound_environments(version.agent_definition_id):
        if environment_policy(environment) != POLICY_FOLLOW:
            pinned.append(str(environment.id))
            continue
        fit = environment_compatibility(environment, version, document=document)
        # Only a change read as compatible moves a follower; a major change, and
        # one that can't be judged yet, is offered and never applied. A world
        # never built has nothing the move could break.
        if fit["status"] == CLEAN or (
            fit["status"] == UNMEASURED and fit.get("reason") == "environment_not_built"
        ):
            following.append(str(environment.id))
        else:
            offered[str(environment.id)] = (
                "unmeasured" if fit["status"] == UNMEASURED else "major_change"
            )
    if following:
        asked_at = timezone.now().isoformat()
        stored = runtime.agent_payload or {}
        HarnessAgentVersionRuntime.no_workspace_objects.filter(id=runtime.id).update(
            agent_payload={
                **stored,
                "requested_by_environments": following,
                "requested_at": {
                    **(stored.get("requested_at") or {}),
                    **dict.fromkeys(following, asked_at),
                },
            },
            updated_at=timezone.now(),
        )
    _record_activation(version.id, {**dict.fromkeys(pinned, "pinned"), **offered})
    for environment_id in [*pinned, *offered]:
        schedule_environment_nudge(environment_id, version.id, default_moved=False)
    return _activate(version, following)


def promote_to_production(version: AgentVersion) -> list[Any]:
    """Make a connected agent's checked version production by hand, whether or not
    it was production before: followers move again, so a return to an earlier
    version carries them back. Returns the environments moved."""
    runtime = _side_row(version.id)
    if runtime is None:
        make_production(version)
        return []
    return _activate_production(version, runtime)


def mirror_default_status(version: AgentVersion) -> None:
    """Show an environment-owned agent's latest default as its active version."""
    if not environment_owned(version.agent_definition_id):
        return
    Statuses = AgentVersion.StatusChoices
    with transaction.atomic():
        # One mirror per agent at a time, so a shared agent never has two active.
        advisory_xact_lock(f"harness-agent-mirror:{version.agent_definition_id}")
        now = timezone.now()
        AgentVersion._base_manager.filter(
            agent_definition_id=version.agent_definition_id, status=Statuses.ACTIVE
        ).exclude(id=version.id).update(status=Statuses.ARCHIVED, updated_at=now)
        AgentVersion._base_manager.filter(id=version.id).update(
            status=Statuses.ACTIVE, updated_at=now
        )


def _own_snapshot_facts(
    snapshot: dict[str, Any],
    description: str,
    source_kind: str,
    source: Mapping[str, Any] | None,
) -> None:
    """The snapshot reads the definition: a version's own prompt and phone number are
    what it stands for. Without a prompt of its own it keeps the definition's."""
    if description:
        snapshot["description"] = description
    phone_number = (source or {}).get("phone_number")
    if source_kind == HarnessAgentVersionRuntime.SourceKind.PHONE and phone_number:
        snapshot["contact_number"] = phone_number


def resnapshot_version(version_id: Any, definition_id: Any = None) -> None:
    """Rebuild a version's snapshot from a fresh read, since the version in hand
    cached its agent and provider row before they were written; never a new version."""
    versions = AgentVersion.no_workspace_objects.select_related("agent_definition")
    if definition_id is not None:
        versions = versions.filter(agent_definition_id=definition_id)
    fresh = versions.filter(id=version_id).first()
    if fresh is None:
        return
    snapshot = fresh.create_snapshot(commit_message=fresh.commit_message)
    row = _side_row(fresh.id)
    if (
        row is not None
        and row.preflight_status != Status.LEGACY
        and row.trigger != Trigger.CREATION
    ):
        # A changed version keeps its own prompt and phone number, as when made.
        _own_snapshot_facts(snapshot, fresh.description, row.source_kind, row.source)
    if snapshot != fresh.configuration_snapshot:
        AgentVersion._base_manager.filter(id=fresh.id).update(
            configuration_snapshot=snapshot
        )


def lineage_version_ids(
    environment: HostedHarnessJob, definition_id: Any
) -> set[str] | None:
    """The versions of an environment-owned agent this environment lists and picks:
    its default and those it asked for. ``None`` for any other agent."""
    if definition_id is None or not environment_owned(definition_id):
        return None
    # Not the default's lineage: a creation reusing another environment's version
    # would take in that environment's ancestors.
    allowed = (
        {str(environment.agent_version_id)} if environment.agent_version_id else set()
    )
    for (
        version_id,
        requested_by,
        created_for,
    ) in HarnessAgentVersionRuntime.no_workspace_objects.filter(
        agent_definition_id=definition_id,
        organization_id=environment.organization_id,
    ).values_list(
        "agent_version_id",
        "agent_payload__requested_by_environments",
        "agent_payload__created_for_environment",
    ):
        if requested_here(requested_by, created_for, environment.id):
            allowed.add(str(version_id))
    return allowed


def environment_credential_values(job: HostedHarnessJob) -> dict[str, str] | None:
    """The values names-only refs of a job no version answers for are read from: its
    own pin's, else its environment's default's. ``None`` without a credential set."""
    version_id = job.agent_version_id
    if version_id is None and job.environment_id is not None:
        version_id = (
            HostedHarnessJob.no_workspace_objects.filter(
                id=job.environment_id, organization_id=job.organization_id
            )
            .values_list("agent_version_id", flat=True)
            .first()
        )
    if version_id is None:
        return None
    return _version_values(job.organization_id, version_id)


def submitted_credentials(
    organization: Any,
    secret_refs: Mapping[str, Mapping[str, Any]],
    secret_values: Mapping[str, str],
) -> SubmittedCredentials:
    """A creation's values, over what its stored refs (staged files, an older client's
    vault refs) hold. A names-only ref without a value is refused."""
    for alias, ref in sorted(secret_refs.items()):
        if is_names_only_ref(ref) and alias not in secret_values:
            raise HostedHarnessError(
                "credential_missing",
                f"A stored value for {alias} is missing; enter it again",
                status_code=422,
                details={"name": alias},
            )
    stored = {
        alias: ref for alias, ref in secret_refs.items() if not is_names_only_ref(ref)
    }
    secret_set, provider_secrets = _resolve_refs(organization, stored, secret_values)
    _overlay(secret_set, provider_secrets, secret_values, ())
    return SubmittedCredentials(
        secret_set=secret_set, provider_secrets=provider_secrets
    )


def check_creation_credentials(
    organization: Any,
    payload: Mapping[str, Any],
    submitted: SubmittedCredentials,
    source_analysis: Any,
) -> CreationCheck:
    """Check a creation's credentials live, reusing its source scan; refused with 422
    when they fail."""
    from simulate.serializers.harness_job import present_provider_aliases
    from simulate.services.harness_provider import _connector_credential_readiness

    detected, required_files, scanned = (
        source_analysis
        if isinstance(source_analysis, tuple | list) and len(source_analysis) == 3
        else ([], [], 0)
    )
    scan = {
        "detected_connectors": sorted(str(name) for name in detected or []),
        "required_files": sorted(str(name) for name in required_files or []),
        "scanned_files": int(scanned or 0),
    }
    try:
        passed, report = _run_preflight(
            organization,
            {
                **payload,
                "credential_values": {
                    **submitted.secret_set.env,
                    **submitted.provider_secrets,
                },
            },
            scan=scan,
        )
    except Exception as exc:  # noqa: BLE001 - its text may carry a value
        logger.warning("harness_creation_preflight_error error=%s", type(exc).__name__)
        passed, report = False, _report([], [], [], "preflight_error", scan)
    if not passed:
        raise HostedHarnessError(
            "agent_preflight_failed",
            "The agent's credentials didn't pass their check",
            status_code=422,
            details={"preflight_report": report},
        )
    readiness = _connector_credential_readiness(
        payload,
        scan["detected_connectors"],
        scan["scanned_files"],
        scan["required_files"],
    )
    agent = payload["agent"]
    # A name public config meets (a LiveKit URL) is never asked of the credentials.
    met_by_config = present_provider_aliases(agent) - set(
        agent.get("secret_refs") or {}
    )
    required = {
        str(item["environment_name"])
        for item in readiness["report"]["requirements"]
        if item.get("required")
    }
    return CreationCheck(
        report=report, required_variable_names=sorted(required - met_by_config)
    )


def _connector_agent_type(connector: str) -> str:
    Types = AgentDefinition.AgentTypeChoices
    return Types.TEXT if connector in ("retell_chat", "auto") else Types.VOICE


def connector_provider(connector: str) -> str:
    """The provider an agent on this connector is stored under."""
    return "retell" if connector == "retell_chat" else connector


def target_identity(
    agent: Mapping[str, Any], authored_direction: str = ""
) -> tuple[str, str, bool | None, bool | None]:
    """A target's assistant id, phone number, inbound (the explicit bool, else its
    call direction, else the authored one) and whether it speaks first."""
    config = agent.get("config") or {}
    explicit_inbound = config.get("inbound")
    direction = (
        str(agent.get("call_direction") or "").strip().lower()
        or str(authored_direction or "").strip().lower()
    )
    if isinstance(explicit_inbound, bool):
        inbound = explicit_inbound
    elif direction in {"inbound", "outbound"}:
        inbound = direction == "inbound"
    else:
        inbound = None
    speaks_first = config.get("target_speaks_first")
    return (
        str(config.get("assistant_id") or config.get("agent_id") or "").strip(),
        str(config.get("phone_number") or "").strip(),
        inbound,
        speaks_first if isinstance(speaks_first, bool) else None,
    )


def advisory_xact_lock(name: str) -> None:
    """Hold a named lock until the current transaction ends."""
    from django.db import connection

    lock_id = int.from_bytes(
        hashlib.sha256(name.encode("utf-8")).digest()[:8], "big", signed=True
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_id])


def _shared_key(payload: Mapping[str, Any]) -> tuple[str, str, str, str] | None:
    """What a provider agent environments share is keyed by; ``None`` when each
    environment has its own agent."""
    agent = payload.get("agent") or {}
    connector = str(agent.get("connector") or "")
    if (
        str((payload.get("source") or {}).get("kind") or "") == "remote"
        or connector not in _SHARED_CONNECTORS
    ):
        return None
    assistant_id, contact_number, _, _ = target_identity(agent)
    if not assistant_id:
        return None
    return (
        connector_provider(connector),
        assistant_id,
        _connector_agent_type(connector),
        contact_number,
    )


def _definition_key(definition: AgentDefinition) -> tuple[str, str, str, str]:
    return (
        definition.provider or "",
        definition.assistant_id or "",
        definition.agent_type,
        definition.contact_number or "",
    )


def _definition_columns(payload: Mapping[str, Any]) -> dict[str, Any]:
    agent = payload.get("agent") or {}
    connector = str(agent.get("connector") or "")
    assistant_id, contact_number, inbound, speaks_first = target_identity(agent)
    return {
        "provider": None if connector == "auto" else connector_provider(connector),
        "assistant_id": assistant_id or None,
        "contact_number": contact_number or None,
        "inbound": True if inbound is None else inbound,
        "target_speaks_first": speaks_first,
        "description": configured_prompt(payload),
    }


def _requested_definition(
    job: HostedHarnessJob, raw_id: Any, connector: str
) -> tuple[AgentDefinition | None, str | None]:
    """The agent a creation names, when it is one of the job's workspace that can run
    the connector; else why not."""
    import uuid

    try:
        definition_id = uuid.UUID(str(raw_id))
    except (TypeError, ValueError, AttributeError):
        return None, "not_found"
    definition = AgentDefinition.no_workspace_objects.filter(
        id=definition_id,
        organization_id=job.organization_id,
        workspace_id=job.workspace_id,
        deleted=False,
    ).first()
    if definition is None:
        return None, "not_found"
    if connector != "auto" and definition.agent_type != _connector_agent_type(
        connector
    ):
        return None, "modality"
    return definition, None


def _shared_definition(
    job: HostedHarnessJob, key: tuple[str, str, str, str]
) -> AgentDefinition | None:
    """The environment-owned agent of this assistant, under a lock no other creation
    of it passes until this one commits."""
    from django.db.models import Q

    provider, assistant_id, agent_type, contact_number = key
    advisory_xact_lock(
        f"harness-agent:{job.organization_id}:{job.workspace_id}:{provider}:"
        f"{assistant_id}:{agent_type}:{contact_number}"
    )
    owned = HarnessAgentVersionRuntime._base_manager.filter(
        organization_id=job.organization_id,
        agent_payload__definition_owner="environment",
    ).values("agent_definition_id")
    number = (
        Q(contact_number=contact_number)
        if contact_number
        else Q(contact_number__isnull=True) | Q(contact_number="")
    )
    return (
        AgentDefinition.no_workspace_objects.filter(
            number,
            organization_id=job.organization_id,
            workspace_id=job.workspace_id,
            provider=provider,
            assistant_id=assistant_id,
            agent_type=agent_type,
            deleted=False,
            id__in=owned,
        )
        .order_by("created_at")
        .first()
    )


def _provider_identifiers(connector: str, config: Mapping[str, Any]):
    """The non-secret target identifiers a version's provider row carries."""
    from simulate.services.types.agent_definition import ProviderCredentialsInput

    if connector == "livekit":
        agent_name = str(config.get("agent_name") or "").strip()
        return (
            ProviderCredentialsInput(provider="livekit", livekit_agent_name=agent_name)
            if agent_name
            else None
        )
    assistant_id = target_identity({"config": config})[0]
    if connector not in _SHARED_CONNECTORS or not assistant_id:
        return None
    return ProviderCredentialsInput(
        provider=connector_provider(connector),
        assistant_id=assistant_id,
    )


def _bind_connected_agent(
    job: HostedHarnessJob, definition: AgentDefinition, metadata: dict[str, Any]
) -> AgentVersion:
    """Pin a new environment to a connected agent's version: the one the creation
    names, else production. The environment takes the version's source and
    credential names and writes nothing to the agent; it follows production unless
    the creation pins it."""
    payload = job.payload
    agent = dict(payload.get("agent") or {})
    requested = metadata.get("agent_version_id")
    version = None
    if requested:
        version = AgentVersion.no_workspace_objects.filter(
            id=requested, agent_definition=definition
        ).first()
        if version is None:
            raise HostedHarnessError(
                "version_not_found",
                "that version is not one of this agent's",
                status_code=404,
            )
    version = version or definition.active_version or definition.latest_version
    if version is None:
        raise HostedHarnessError(
            "agent_has_no_version",
            "This agent has no version to build from yet",
            status_code=409,
        )
    runtime = _side_row(version.id)
    if runtime is not None and runtime.preflight_status not in (
        Status.PASSED,
        Status.LEGACY,
    ):
        raise HostedHarnessError(
            "version_not_ready",
            "This version has not passed its check yet",
            status_code=409,
            details={"preflight_status": runtime.preflight_status},
        )
    source = dict(payload.get("source") or {})
    if runtime is not None and runtime.source_kind in (
        HarnessAgentVersionRuntime.SourceKind.GITHUB,
        HarnessAgentVersionRuntime.SourceKind.ARCHIVE,
    ):
        access = (runtime.agent_payload or {}).get("source_access") or {}
        source = {
            **source,
            "kind": str(runtime.source_kind),
            **{name: value for name, value in (runtime.source or {}).items() if value},
            **{
                name: access[name]
                for name in ("visibility", "installation_id")
                if access.get(name)
            },
        }
    names = version_credential_names(runtime) if runtime is not None else []
    metadata.update(
        agent_definition_owner=CONNECTED_OWNER,
        agent_definition_id=str(definition.id),
        build_agent_version_id=str(version.id),
    )
    metadata.setdefault(POLICY_KEY, POLICY_FOLLOW)
    job.agent_version = version
    job.payload = {
        **payload,
        "source": source,
        "agent": {
            **agent,
            "secret_refs": {name: agent_version_secret_ref(name) for name in names},
        },
        "metadata": metadata,
    }
    job.save(update_fields=["agent_version", "payload", "updated_at"])
    logger.info(
        "harness_environment_agent_bound job=%s definition=%s version=%s names=%d",
        job.id,
        definition.id,
        version.id,
        len(names),
    )
    return version


def ensure_environment_agent(
    job: HostedHarnessJob,
    *,
    submitted: SubmittedCredentials,
    check: CreationCheck,
    user: Any = None,
    repository_id: int | None = None,
) -> AgentVersion:
    """Make a new environment's agent and its first version, and pin the job to it.

    Inside the creation's transaction. A provider agent is shared by the environments
    on its assistant; any other agent is the environment's own. Its columns are
    written when it is made, never when it is reused.
    """
    from simulate.services.agent_definition import sync_provider_credentials
    from simulate.services.harness_versioning.backfill import legacy_agent_payload
    from simulate.services.hosted_harness import _derive_name_from_job

    payload = job.payload
    agent = payload.get("agent") or {}
    connector = str(agent.get("connector") or "")
    config = agent.get("config") or {}
    metadata = dict(payload.get("metadata") or {})
    key = _shared_key(payload)
    definition = None
    requested_id = None
    raw_id = metadata.get("agent_definition_id")
    if raw_id:
        found, reason = _requested_definition(job, raw_id, connector)
        if found is not None:
            if not environment_owned(found.id):
                return _bind_connected_agent(job, found, metadata)
            elif key is not None and key == _definition_key(found):
                definition = found
            else:
                reason = "environment_owned"
        if definition is None:
            logger.info(
                "harness_agent_not_bound job=%s reason=%s definition=%s",
                job.id,
                reason,
                getattr(found, "id", None),
            )
            requested_id = str(raw_id)
    if definition is None and key is not None:
        definition = _shared_definition(job, key)
    reused_definition = definition is not None
    if definition is None:
        name = metadata.get("agent_name") or metadata.get("name")
        definition = AgentDefinition.no_workspace_objects.create(
            organization=job.organization,
            workspace=job.workspace,
            agent_name=str(name or _derive_name_from_job(job))[:255],
            agent_type=_connector_agent_type(connector),
            **_definition_columns(payload),
        )
    version = create_agent_version(
        definition,
        parent=None,
        env=job,
        # GitHub's id for the repository, looked up at creation: kept with how the
        # source is reached, never in the environment's own payload.
        source={**(payload.get("source") or {}), "repository_id": repository_id},
        prompt=configured_prompt(payload) or None,
        agent_payload={
            **legacy_agent_payload(payload),
            "definition_owner": "environment",
        },
        secret_values={**submitted.secret_set.env, **submitted.provider_secrets},
        secret_files=list(submitted.secret_set.files.values()),
        trigger=Trigger.CREATION,
        user=user,
        activate=False,
    )
    runtime = _side_row(version.id, lock=True)
    stored = dict(runtime.agent_payload or {})
    created = stored.get("created_for_environment") == str(job.id)
    requested = list(stored.get("requested_by_environments") or [])
    # Asked for here, so an activation of it never reads as a rerun's.
    stored["requested_by_environments"] = (
        requested if str(job.id) in requested else [*requested, str(job.id)]
    )
    stored["requested_defaults"] = {
        **(stored.get("requested_defaults") or {}),
        str(job.id): None,
    }
    now = timezone.now()
    finished: dict[str, Any] = {"agent_payload": stored, "updated_at": now}
    if runtime.preflight_status == Status.PENDING:
        finished.update(
            preflight_status=Status.PASSED,
            preflighted_at=now,
            preflight_report=check.report,
            required_variable_names=check.required_variable_names,
        )
    HarnessAgentVersionRuntime.no_workspace_objects.filter(id=runtime.id).update(
        **finished
    )
    row = _own_provider_row(version.id)
    identifiers = _provider_identifiers(connector, config)
    if (
        created
        and identifiers is not None
        and row is not None
        and row.provider_type == _CONNECTOR_PROVIDERS.get(connector)
    ):
        sync_provider_credentials(version, identifiers)
        resnapshot_version(version.id)
    mirror_default_status(version)
    metadata.update(
        agent_definition_owner="environment",
        agent_definition_id=str(definition.id),
        build_agent_version_id=str(version.id),
    )
    if requested_id is not None:
        metadata["requested_agent_definition_id"] = requested_id
    names = version_credential_names(runtime)
    job.agent_version = version
    job.payload = {
        **payload,
        "agent": {
            **agent,
            "secret_refs": {name: agent_version_secret_ref(name) for name in names},
        },
        "metadata": metadata,
    }
    job.save(update_fields=["agent_version", "payload", "updated_at"])
    logger.info(
        "harness_environment_agent_created job=%s definition=%s version=%s"
        " reused_definition=%s reused_version=%s names=%d",
        job.id,
        definition.id,
        version.id,
        reused_definition,
        not created,
        len(names),
    )
    return version


def stamp_built_commit(job: HostedHarnessJob, commit_sha: str | None) -> None:
    """Record the commit an environment's build resolved its branch to in the agent
    versions made for it that name the branch alone, so their runs fetch that commit
    rather than the branch's tip. A version that names a commit, or that another
    environment uses, keeps what it names."""
    from django.db.models import Q

    from simulate.services.harness_versioning.versions import versioning_enabled

    source = (job.payload or {}).get("source") or {}
    if (
        not versioning_enabled()
        or not commit_sha
        or job.environment_id is not None
        or not job.agent_version_id
        or source.get("kind") != "github"
    ):
        return
    with transaction.atomic():
        # The lock create_agent_version takes, so no child copies the branch meanwhile.
        definition = (
            AgentDefinition.no_workspace_objects.select_for_update(of=("self",))
            .filter(versions__id=job.agent_version_id)
            .first()
        )
        if definition is None:
            return
        rows = (
            HarnessAgentVersionRuntime.no_workspace_objects.filter(
                Q(agent_payload__created_for_environment=str(job.id))
                | Q(agent_version_id=job.agent_version_id),
                agent_definition_id=definition.id,
                source_kind=HarnessAgentVersionRuntime.SourceKind.GITHUB,
                source__repository__iexact=str(source.get("repository") or ""),
            )
            .exclude(preflight_status=Status.LEGACY)
            .select_for_update()
            .defer("credential_blob")
        )
        ref = source.get("ref") or None
        for runtime in rows:
            facts = runtime.source or {}
            if facts.get("commit_sha") or facts.get("ref") != ref:
                continue
            stored = runtime.agent_payload or {}
            if (
                stored.get("created_for_environment") not in (None, str(job.id))
                or any(
                    str(one) != str(job.id)
                    for one in stored.get("requested_by_environments") or []
                )
                or HostedHarnessJob.no_workspace_objects.filter(
                    agent_version_id=runtime.agent_version_id,
                    environment__isnull=True,
                )
                .exclude(id=job.id)
                .exists()
            ):
                # TODO: fork a version carrying this commit for this environment when
                # another environment shares the row; it keeps the branch until then.
                logger.warning(
                    "harness_agent_commit_not_stamped job=%s version=%s reason=shared",
                    job.id,
                    runtime.agent_version_id,
                )
                continue
            runtime.source = {**facts, "commit_sha": commit_sha}
            runtime.hash = _runtime_hash(runtime)
            try:
                with transaction.atomic():
                    HarnessAgentVersionRuntime.no_workspace_objects.filter(
                        id=runtime.id
                    ).update(
                        source=runtime.source,
                        hash=runtime.hash,
                        updated_at=timezone.now(),
                    )
            except IntegrityError:
                # A live version already names this commit; this one keeps the branch.
                logger.warning(
                    "harness_agent_commit_not_stamped job=%s version=%s",
                    job.id,
                    runtime.agent_version_id,
                )
