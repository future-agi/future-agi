"""Operational E2E verification harness across all simulation providers (TH-5642).

The platform code for every provider × modality × direction is implemented and
unit-tested, but *live* end-to-end verification needs real provider credentials and (for
telephony) a deployed SIP stack — things that live outside the codebase. This harness
turns that manual, ad-hoc checking into one repeatable, creds-driven report:

    python manage.py verify_providers --mode registry      # declared matrix, no I/O
    python manage.py verify_providers --mode credentials   # which creds are present
    python manage.py verify_providers --mode connectivity  # real handshakes (creds)

The matrix is derived from the provider registry (the single source of truth), so it
stays correct as providers are added/promoted. Credentials are read from environment
variables under the ``SIM_VERIFY_<PROVIDER>_<FIELD>`` convention — deliberately separate
from customer-owned ProviderCredentials, which must not be used for arbitrary test calls.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from simulate.providers import registry as reg

# Verification modes, cheapest/safest first.
MODE_REGISTRY = "registry"          # pure: what the registry declares is implemented
MODE_CREDENTIALS = "credentials"    # are the env credentials present for a real run?
MODE_CONNECTIVITY = "connectivity"  # real handshake against the provider (needs creds)
MODES = (MODE_REGISTRY, MODE_CREDENTIALS, MODE_CONNECTIVITY)

# Cell status values.
OK = "ok"              # implemented / creds present / handshake succeeded
MISSING = "missing"    # creds absent
FAILED = "failed"      # handshake attempted and failed
SKIPPED = "skipped"    # not applicable / no probe / needs deployed stack
NOT_IMPL = "not_implemented"  # registry does not implement this cell

# Required credential env-var field suffixes per registry CredentialShape. The full env
# var is SIM_VERIFY_<PROVIDER>_<SUFFIX>, e.g. SIM_VERIFY_DEEPGRAM_API_KEY.
_SHAPE_FIELDS: dict[str, tuple[str, ...]] = {
    "api_key_assistant": ("API_KEY",),
    "agent_id": ("API_KEY", "AGENT_ID"),
    "websocket_url": ("WEBSOCKET_URL",),
    "livekit_server": ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"),
    "sip_only": (),   # no API credential — needs a deployed SIP stack instead
    "none": (),
}

# Shapes whose live path additionally needs a deployed SIP/telephony stack, which the
# harness cannot stand up on its own.
_NEEDS_SIP_STACK = {"sip_only"}


@dataclass(frozen=True)
class VerificationCell:
    provider: str
    modality: str        # "chat" | "voice"
    direction: str | None  # "inbound" | "outbound" for voice; None for chat
    status: str
    detail: str = ""


@dataclass
class VerificationReport:
    mode: str
    cells: list[VerificationCell] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.cells:
            out[c.status] = out.get(c.status, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "summary": self.summary(),
            "cells": [
                {
                    "provider": c.provider,
                    "modality": c.modality,
                    "direction": c.direction,
                    "status": c.status,
                    "detail": c.detail,
                }
                for c in self.cells
            ],
        }


def _env_for(provider: str, suffix: str) -> str:
    return f"SIM_VERIFY_{provider.upper()}_{suffix}"


def credential_status(
    provider: str, env: Mapping[str, str] | None = None
) -> tuple[str, str]:
    """Is the credential set for ``provider`` present? Returns (status, detail)."""
    env = env if env is not None else os.environ
    spec = reg.get_spec(provider)
    shape = str(getattr(spec, "credential_shape", "none"))
    if shape in _NEEDS_SIP_STACK:
        return SKIPPED, "requires a deployed SIP/telephony stack (no API credential)"
    fields = _SHAPE_FIELDS.get(shape, ())
    if not fields:
        return OK, "no credential required"
    missing = [_env_for(provider, f) for f in fields if not env.get(_env_for(provider, f))]
    if missing:
        return MISSING, f"set {', '.join(missing)}"
    return OK, f"credentials present ({shape})"


def _modality_cells(provider: str) -> list[tuple[str, str | None]]:
    """The (modality, direction) cells the registry declares for a provider."""
    spec = reg.get_spec(provider)
    cells: list[tuple[str, str | None]] = []
    if bool(getattr(spec, "chat", False)):
        cells.append(("chat", None))
    impl = {str(d) for d in reg.implemented_directions_for(provider)}
    for direction in ("inbound", "outbound"):
        if direction in impl:
            cells.append(("voice", direction))
    return cells


def declared_matrix() -> VerificationReport:
    """Registry mode: what is implemented, no I/O. Always runnable."""
    report = VerificationReport(mode=MODE_REGISTRY)
    for provider in reg.agent_platform_keys():
        spec = reg.get_spec(provider)
        status = str(getattr(spec, "status", ""))
        cells = _modality_cells(provider)
        if not cells:
            report.cells.append(
                VerificationCell(provider, "—", None, NOT_IMPL, f"status={status}")
            )
            continue
        for modality, direction in cells:
            report.cells.append(
                VerificationCell(provider, modality, direction, OK, f"status={status}")
            )
    return report


def credentials_matrix(env: Mapping[str, str] | None = None) -> VerificationReport:
    """Credentials mode: for each declared cell, are the creds present?"""
    report = VerificationReport(mode=MODE_CREDENTIALS)
    for provider in reg.agent_platform_keys():
        cstatus, cdetail = credential_status(provider, env)
        for modality, direction in _modality_cells(provider):
            report.cells.append(
                VerificationCell(provider, modality, direction, cstatus, cdetail)
            )
    return report


# Connectivity probes: provider -> callable(env) -> (ok: bool, detail: str). Real network
# handshakes live here; only providers with a proven probe are registered. Others report
# SKIPPED so the report never implies coverage it does not have.
ConnectivityProbe = Callable[[Mapping[str, str]], "tuple[bool, str]"]


def connectivity_matrix(
    env: Mapping[str, str] | None = None,
    probes: Mapping[str, ConnectivityProbe] | None = None,
) -> VerificationReport:
    """Connectivity mode: run a real handshake where a probe exists; else SKIP."""
    env = env if env is not None else os.environ
    probes = probes if probes is not None else default_connectivity_probes()
    report = VerificationReport(mode=MODE_CONNECTIVITY)
    for provider in reg.agent_platform_keys():
        cells = _modality_cells(provider)
        probe = probes.get(provider)
        cstatus, cdetail = credential_status(provider, env)
        for modality, direction in cells:
            if cstatus == MISSING:
                report.cells.append(
                    VerificationCell(provider, modality, direction, MISSING, cdetail)
                )
                continue
            if probe is None:
                report.cells.append(
                    VerificationCell(
                        provider, modality, direction, SKIPPED,
                        "no connectivity probe (needs live call / deployed stack)",
                    )
                )
                continue
            try:
                ok, detail = probe(env)
            except Exception as exc:  # network/handshake failure
                ok, detail = False, f"{type(exc).__name__}: {exc}"
            report.cells.append(
                VerificationCell(
                    provider, modality, direction, OK if ok else FAILED, detail
                )
            )
    return report


def default_connectivity_probes() -> dict[str, ConnectivityProbe]:
    """Real handshake probes proven to work credential-only (no calls placed).

    Imported lazily so importing this module performs no network I/O.
    """
    from simulate.services import provider_connectivity_probes as p

    return {
        "deepgram": p.deepgram_probe,
        "elevenlabs": p.elevenlabs_probe,
        "vapi": p.vapi_probe,
        "retell": p.retell_probe,
        "bland": p.bland_probe,
        "twilio": p.twilio_probe,
        "agora": p.agora_probe,
        "livekit_bridge": p.make_livekit_probe("livekit_bridge"),
        "pipecat": p.make_livekit_probe("pipecat"),
    }


def verify(mode: str, env: Mapping[str, str] | None = None) -> VerificationReport:
    if mode == MODE_REGISTRY:
        return declared_matrix()
    if mode == MODE_CREDENTIALS:
        return credentials_matrix(env)
    if mode == MODE_CONNECTIVITY:
        return connectivity_matrix(env)
    raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
