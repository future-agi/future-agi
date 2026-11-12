"""Strict service boundary with no route, global singleton or implicit adapters."""

import base64
import json
from uuid import UUID

from .gateway_content_bridge_types import (
    Decision,
    GatewayActor,
    Ports,
    PortUnavailable,
    ProvenIdentity,
    Request,
    Target,
)

ACTIONS = {
    "LIST_RESOURCE_METADATA": "INVENTORY",
    "LIST_PROMPT_METADATA": "INVENTORY",
    "RESOURCE_CONTENT_READ": "RESOURCE",
    "PROMPT_GET_RENDER": "PROMPT",
    "MANAGE_CONTENT_CONFIGURATION": "CONFIGURATION",
    "TOOL_INVOKE": "TOOL",
}
DENIALS = frozenset(
    {
        "INVALID_REQUEST",
        "PRINCIPAL_INACTIVE",
        "CREDENTIAL_NOT_ACTIVE",
        "CREDENTIAL_BINDING_MISMATCH",
        "ORG_ACCESS_DENIED",
        "WORKSPACE_ACCESS_DENIED",
        "TARGET_NOT_ACCESSIBLE",
        "ACTION_NOT_ALLOWED",
        "POLICY_NOT_DEFINED",
        "ARGUMENTS_OUT_OF_BOUNDS",
    }
)
UNAVAILABLES = frozenset(
    {"AUTHORITY_LOOKUP_FAILED", "TARGET_LOOKUP_FAILED", "REVISION_UNAVAILABLE"}
)
UNAVAILABLE = Decision("UNAVAILABLE", "AUTHORITY_LOOKUP_FAILED", None)
INVALID = Decision("DENIED", "INVALID_REQUEST", None)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _text(value, maximum=512):
    if type(value) is not str or not value or len(value) > maximum:
        raise ValueError("invalid string")
    if any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError("invalid string")
    return value


def _uuid(value):
    parsed = UUID(_text(value, 36))
    if parsed.int == 0 or str(parsed) != value:
        raise ValueError("invalid identifier")
    return parsed


def parse_request(raw):
    if type(raw) is not bytes or len(raw) > 8192:
        raise ValueError("invalid request")
    data = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_object)
    expected = {
        "version",
        "actor",
        "org_id",
        "workspace_id",
        "action",
        "target",
        "phase",
        "request_id",
        "unit_id",
    }
    if type(data) is not dict or set(data) != expected:
        raise ValueError("invalid fields")
    if data["version"] != "gateway-content-authorization-v1":
        raise ValueError("invalid version")
    actor = data["actor"]
    if type(actor) is not dict:
        raise ValueError("invalid actor")
    if actor.get("kind") == "oauth":
        raise PortUnavailable()
    if (
        set(actor) != {"kind", "person_id", "gateway_key_id"}
        or actor["kind"] != "gateway_key"
    ):
        raise ValueError("invalid actor")
    action, phase = _text(data["action"]), _text(data["phase"])
    if action not in ACTIONS or phase not in {"INITIAL", "RELEASE", "CONTINUATION"}:
        raise ValueError("invalid action or phase")
    return Request(
        GatewayActor(_uuid(actor["person_id"]), _text(actor["gateway_key_id"])),
        _uuid(data["org_id"]),
        _uuid(data["workspace_id"]),
        action,
        _text(data["target"]),
        phase,
        _text(data["request_id"]),
        _text(data["unit_id"]),
    )


def valid_decision(result):
    if type(result) is not Decision:
        return False
    if type(result.decision) is not str or type(result.reason_code) is not str:
        return False
    revision = result.membership_revision
    if revision is not None and (type(revision) is not bytes or len(revision) > 4096):
        return False
    if result.decision == "ALLOW":
        return result.reason_code == "AUTHORIZED"
    if revision is not None:
        return False
    return (result.decision == "DENIED" and result.reason_code in DENIALS) or (
        result.decision == "UNAVAILABLE" and result.reason_code in UNAVAILABLES
    )


def encode_decision(result):
    if not valid_decision(result):
        result = UNAVAILABLE
    revision = result.membership_revision
    return {
        "decision": result.decision,
        "reason_code": result.reason_code,
        "membership_revision": None
        if revision is None
        else base64.b64encode(revision).decode("ascii"),
    }


class ContentBridge:
    def __init__(self, ports: Ports):
        if type(ports) is not Ports or any(
            getattr(ports, name) is None for name in Ports.__dataclass_fields__
        ):
            raise ValueError("all explicit authority ports are required")
        self.ports = ports

    def authorize(self, evidence: bytes, raw: bytes) -> Decision:
        try:
            transport = self.ports.transport.authenticate(evidence)
            if transport is None:
                return Decision("DENIED", "CREDENTIAL_NOT_ACTIVE", None)
            request = parse_request(raw)
        except PortUnavailable:
            return UNAVAILABLE
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return INVALID
        except Exception:
            return UNAVAILABLE
        try:
            return self._authorize(transport, request)
        except Exception:
            # No raw dependency errors, credential proof or prompt data escapes.
            return UNAVAILABLE

    def _authorize(self, transport, request):
        identity = self.ports.gateway_keys.resolve(transport, request.actor)
        if identity is None:
            return Decision("DENIED", "CREDENTIAL_NOT_ACTIVE", None)
        if type(identity) is not ProvenIdentity or identity.kind not in {
            "personal_key",
            "session",
        }:
            raise PortUnavailable()
        if any(
            type(v) is not UUID or v.int == 0
            for v in (identity.person_id, identity.org_id, identity.credential_id)
        ):
            raise PortUnavailable()
        if (
            identity.person_id != request.actor.person_id
            or identity.org_id != request.org_id
            or (
                identity.pinned_workspace_id is not None
                and identity.pinned_workspace_id != request.workspace_id
            )
        ):
            return Decision("DENIED", "CREDENTIAL_BINDING_MISMATCH", None)
        workspace = self.ports.workspaces.resolve(identity, request.workspace_id)
        if type(workspace) is not UUID or workspace != request.workspace_id:
            return Decision("DENIED", "WORKSPACE_ACCESS_DENIED", None)
        kind = ACTIONS[request.action]

        def resolve_target(handle, org, workspace):
            # Called by canonical authority under its primary transaction.
            if (handle, org, workspace) != (
                request.target,
                request.org_id,
                request.workspace_id,
            ):
                raise PortUnavailable()
            target = self.ports.targets.resolve(handle, org, workspace, kind)
            if target is None:
                return None
            if (
                type(target) is not Target
                or target.organization_id != org
                or target.kind != kind
            ):
                raise PortUnavailable()
            return target

        arguments = None
        if request.action == "PROMPT_GET_RENDER":
            arguments = self.ports.prompts.resolve(identity, request)
            if arguments is None:
                raise PortUnavailable()
            if type(arguments) is not dict or len(arguments) > 32:
                return Decision("DENIED", "ARGUMENTS_OUT_OF_BOUNDS", None)
            if any(
                type(k) is not str or type(v) is not str for k, v in arguments.items()
            ):
                return Decision("DENIED", "ARGUMENTS_OUT_OF_BOUNDS", None)
            try:
                too_large = any(
                    len(k.encode("utf-8")) > 128 or len(v.encode("utf-8")) > 4096
                    for k, v in arguments.items()
                )
                serialized = json.dumps(
                    arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                if too_large or len(serialized) > 16384:
                    return Decision("DENIED", "ARGUMENTS_OUT_OF_BOUNDS", None)
            except UnicodeError:
                return Decision("DENIED", "ARGUMENTS_OUT_OF_BOUNDS", None)
            # Preserve exact values. Canonical still owns approved schema policy.
            arguments = dict(arguments)
        result = self.ports.canonical.authorize(
            identity, request, resolve_target, arguments
        )
        return result if valid_decision(result) else UNAVAILABLE
