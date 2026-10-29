"""Content identity of versioned harness pieces: environment, suite and agent.

Archives are passed as ``{relative posix path: bytes}``. Digests are ``"sha256:" + hex``.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

from simulate.services.canonical_json import canonical_digest, canonical_json_bytes

Files = Mapping[str, bytes]

# Agent provider keys live in ProviderCredentials, not in the runtime credential set.
PROVIDER_CREDENTIAL_ALIASES = frozenset(
    {
        "VAPI_API_KEY",
        "RETELL_API_KEY",
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    }
)

# Fields the guest overlays at call time (ALK call_runner._with_scenario_edits).
SCENARIO_OVERLAY_FIELDS = ("background_noise", "max_turns")
SCENARIO_OVERLAY_PERSONA_FIELDS = (
    "personality",
    "communication_style",
    "accent",
    "languages",
    "occupation",
    "location",
)

# In an authoring tree but not part of the environment's identity: the contract is
# hashed separately; the rest embed the agent's source digest, belong to the suite or
# to a build, or are run noise. ALK keeps its derived records and caches under
# generic-harness/, so a file it adds there never moves the hash.
ENVIRONMENT_UNHASHED_FILES = frozenset(
    {
        "contract.json",
        "runtime-validation.json",
        "cost.json",
        "harness-events.jsonl",
        "scenarios.json",
        "coverage.json",
    }
)
ENVIRONMENT_UNHASHED_ROOTS = (
    "scenarios/",
    "scenario/",
    "environment-bundle/",
    "generic-harness/",
)
WORLD_SEED_FILES = ("world.sqlite", "store.json", "collections.json", "schema.sql")
SCENARIO_ROOTS = ("scenarios", "scenario")


def read_tree(root: Path) -> dict[str, bytes]:
    """Every file under ``root``, keyed by its relative posix path."""
    return {
        item.relative_to(root).as_posix(): item.read_bytes()
        for item in root.rglob("*")
        if item.is_file()
    }


def _is_noise(path: str) -> bool:
    return any(
        part == "__pycache__" or part.startswith(".")
        for part in PurePosixPath(path).parts
    )


def is_environment_unhashed(path: str) -> bool:
    """Stored in an environment archive but not part of its identity."""
    return _environment_unhashed(path)


def _environment_unhashed(path: str) -> bool:
    return (
        path in ENVIRONMENT_UNHASHED_FILES
        or path.startswith(ENVIRONMENT_UNHASHED_ROOTS)
        or _is_noise(path)
    )


def _invalid_path(path: str, why: str) -> Exception:
    from simulate.services.hosted_harness import HostedHarnessError

    return HostedHarnessError(
        "archive_member_invalid",
        f"the authoring archive holds an unsafe member: {path} ({why})",
        status_code=422,
    )


def _contract_value(raw: bytes | None, *, provider_agent: bool) -> Any:
    """The contract as hashed, or a marker of its bytes when it has no canonical form.

    JSON allows NaN and Infinity, which canonical JSON refuses, and an unparsable file has
    no value at all; either way two different files must not hash the same.
    """
    contract = _json_object(raw)
    if contract is not None:
        kept = hashed_contract(contract, provider_agent=provider_agent)
        try:
            canonical_json_bytes(kept)
            return kept
        except ValueError:
            pass
    if raw is None:
        return {}
    return {"unreadable_contract_sha256": hashlib.sha256(raw).hexdigest()}


def tree_digest(
    files: Path | Files, *, exclude: Callable[[str], bool] | None = None
) -> str:
    """Byte-for-byte port of ALK ``authoring_runtime_validation._artifact_digest``.

    Paths are ordered component by component, as ALK's ``sorted(Path)`` does.
    """
    digest = hashlib.sha256()
    if isinstance(files, Path):
        if files.is_file():
            digest.update(files.read_bytes())
            return "sha256:" + digest.hexdigest()
        files = read_tree(files) if files.is_dir() else {}
    normalised: dict[str, bytes] = {}
    for path, body in files.items():
        spelled = PurePosixPath(path).as_posix()
        if spelled in normalised:
            raise _invalid_path(spelled, "two members share this path")
        normalised[spelled] = body
    files = normalised
    for path in sorted(files, key=PurePosixPath):
        if exclude is not None and exclude(path):
            continue
        try:
            relative = path.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise _invalid_path(
                path.encode("utf-8", "backslashreplace").decode("ascii"),
                "the path is not valid UTF-8",
            ) from exc
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(files[path])
    return "sha256:" + digest.hexdigest()


def _json_object(body: bytes | None) -> dict[str, Any] | None:
    try:
        value = json.loads(body) if body else None
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def hashed_contract(
    contract: Mapping[str, Any], *, provider_agent: bool
) -> dict[str, Any]:
    """The contract as it counts toward the environment hash.

    ``chosen_evals`` is a build-time seed; a provider agent's prompt belongs to the agent.
    """
    kept = {key: value for key, value in contract.items() if key != "chosen_evals"}
    if provider_agent:
        kept.pop("system_prompt_excerpt", None)
    return kept


def environment_hash(files: Files, *, provider_agent: bool) -> str:
    return canonical_digest(
        {
            "contract": _contract_value(
                files.get("contract.json"), provider_agent=provider_agent
            ),
            "tree": tree_digest(files, exclude=_environment_unhashed),
        }
    )


def contract_hash_raw(files: Files) -> str:
    raw = files.get("contract.json")
    contract = _json_object(raw)
    if raw is not None and contract is None:
        return "sha256:" + hashlib.sha256(raw).hexdigest()
    try:
        return canonical_digest(contract or {})
    except ValueError:
        return "sha256:" + hashlib.sha256(raw or b"").hexdigest()


def world_ir_hash(files: Files) -> str:
    return tree_digest(
        {name: files[name] for name in WORLD_SEED_FILES if name in files}
    )


def scenario_files(files: Files) -> dict[str, bytes]:
    """The suite's files, keyed relative to its folder.

    Like ALK, ``scenarios/`` wins and the legacy ``scenario/`` is read only without it.
    """
    for root in SCENARIO_ROOTS:
        prefix = root + "/"
        found = {
            path[len(prefix) :]: body
            for path, body in files.items()
            if path.startswith(prefix) and len(path) > len(prefix)
        }
        if found:
            return found
    return {}


def _is_check(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return len(parts) > 2 and parts[1] == "checks"


def _suite_unhashed(path: str) -> bool:
    return _is_check(path) or _is_noise(path)


def suite_hash(files: Files) -> str:
    """Checks are derived from the environment's sub-goals, so they are not hashed."""
    return tree_digest(scenario_files(files), exclude=_suite_unhashed)


# Stands in for an overlay field's value: which fields are set still counts, since the
# overlay can change a value but cannot remove a field the sealed bundle carries.
OVERLAY_PRESENT = "<overlay>"


def _overlay_keys_only(document: bytes) -> bytes:
    scenario = _json_object(document)
    if scenario is None:
        return document
    for name in SCENARIO_OVERLAY_FIELDS:
        if name in scenario:
            scenario[name] = OVERLAY_PRESENT
    persona = scenario.get("persona")
    if isinstance(persona, dict):
        scenario["persona"] = {
            key: OVERLAY_PRESENT if key in SCENARIO_OVERLAY_PERSONA_FIELDS else value
            for key, value in persona.items()
        }
    try:
        return canonical_json_bytes(scenario)
    except ValueError:
        return document


def suite_certify_hash(files: Files, *, keep_overlay: bool = False) -> str:
    """The suite as the sealed bundle sees it.

    The values of fields the guest overlays at call time are left out, so a value-only
    edit reuses a cached bundle; adding or removing such a field does not.
    ``keep_overlay`` is for connectors whose runner ignores the overlay.
    """
    scenarios = scenario_files(files)
    if not keep_overlay:
        scenarios = {
            path: (
                _overlay_keys_only(body)
                if PurePosixPath(path).name == "scenario.json"
                else body
            )
            for path, body in scenarios.items()
        }
    return tree_digest(scenarios, exclude=_suite_unhashed)


def sub_goal_digests(sub_goals: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """A digest of each sub-goal's definition, keyed by its name."""
    return {
        str(goal["name"]): canonical_digest(
            {key: goal.get(key) for key in ("what", "check", "judged", "output")}
        )
        for goal in sub_goals
    }


def organization_credential_key(organization_id: object) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=f"harness-credentials:{organization_id}".encode(),
    ).derive(settings.SECRET_KEY.encode("utf-8"))


def credential_fingerprint(organization_id: object, value: str | bytes) -> str:
    """Keyed fingerprint of one credential value; safe to store and compare."""
    data = value.encode("utf-8") if isinstance(value, str) else value
    key = organization_credential_key(organization_id)
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def credential_set_hash(fingerprints: Mapping[str, str]) -> str:
    """Identity of a runtime credential set; provider aliases are not part of it."""
    lines = sorted(
        f"{name}:{fingerprint}"
        for name, fingerprint in fingerprints.items()
        if name not in PROVIDER_CREDENTIAL_ALIASES
    )
    return "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def credential_names_hash(names: Iterable[str]) -> str:
    kept = sorted({name for name in names if name not in PROVIDER_CREDENTIAL_ALIASES})
    return "sha256:" + hashlib.sha256("\n".join(kept).encode("utf-8")).hexdigest()


def provider_credentials_fingerprint(
    organization_id: object, api_key: str, provider_agent_id: str
) -> str:
    return credential_fingerprint(organization_id, f"{api_key}\n{provider_agent_id}")


@dataclass(frozen=True)
class AgentIdentity:
    """What makes an agent version distinct."""

    source: str
    connector: str
    mode: str = ""
    call_direction: str = ""
    target_speaks_first: bool | None = None
    config: Mapping[str, Any] = field(default_factory=dict)
    prompt_hash: str | None = None
    credential_fingerprints: Mapping[str, str] = field(default_factory=dict)
    provider_credentials_fingerprint: str | None = None
    provider_alias_names: tuple[str, ...] = ()

    def _common(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "prompt_hash": self.prompt_hash,
            "connector": self.connector,
            "mode": self.mode,
            "call_direction": self.call_direction,
            "target_speaks_first": self.target_speaks_first,
            "config": copy.deepcopy(dict(self.config)),
        }


def agent_hash(identity: AgentIdentity) -> str:
    """Changes with any credential value, so a key change makes a new agent version."""
    return canonical_digest(
        {
            **identity._common(),
            "credential_set_hash": credential_set_hash(
                identity.credential_fingerprints
            ),
            "provider_credentials_fingerprint": (
                identity.provider_credentials_fingerprint
            ),
        }
    )


def agent_cache_hash(identity: AgentIdentity) -> str:
    """Changes with credential names only, so a key rotation reuses a cached bundle."""
    return canonical_digest(
        {
            **identity._common(),
            "credential_names_hash": credential_names_hash(
                identity.credential_fingerprints
            ),
            "provider_alias_names": sorted(identity.provider_alias_names),
        }
    )


def assembly_key(
    agent_cache_digest: str, environment_digest: str, suite_certify_digest: str
) -> str:
    return canonical_digest(
        [agent_cache_digest, environment_digest, suite_certify_digest]
    )
