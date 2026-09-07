"""Static catalog of seed RL-environment agents.

The authoritative metadata for each template comes from its own
``seeds/<slug>/config.json`` (the LiveKit-native agent definition). A small
curated ``_OVERLAY`` adds only the platform-facing presentation bits that the
agent definition does not carry (vertical, icon, display order, and a sensible
default scenario count).

Nothing here talks to object storage; packing a template into a source archive
is the caller/provider's job (see ``harness_provider.instantiate_template``),
which uses :func:`iter_template_files`.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

SEEDS_DIR = Path(__file__).resolve().parent / "seeds"

# Presentation overlay keyed by slug. ``order`` controls gallery position; the
# set of keys here is also the allow-list of templates we expose — a folder
# without an overlay entry is ignored, and an overlay entry without a folder is
# skipped, so the two can never drift into a half-defined template.
_OVERLAY: dict[str, dict[str, Any]] = {
    "debt_collection": {
        "order": 1,
        "vertical": "Financial services",
        "icon": "solar:wallet-money-bold-duotone",
        "default_scenario_count": 12,
    },
    "insurance_fnol": {
        "order": 2,
        "vertical": "Insurance",
        "icon": "solar:shield-check-bold-duotone",
        "default_scenario_count": 12,
    },
    "healthcare_scheduling": {
        "order": 3,
        "vertical": "Healthcare",
        "icon": "solar:health-bold-duotone",
        "default_scenario_count": 12,
    },
    "banking_support": {
        "order": 4,
        "vertical": "Fintech",
        "icon": "solar:card-2-bold-duotone",
        "default_scenario_count": 12,
    },
}


def _seed_dir(slug: str) -> Path:
    return SEEDS_DIR / slug


@lru_cache(maxsize=None)
def _load_config(slug: str) -> dict[str, Any] | None:
    """Read and parse ``seeds/<slug>/config.json``; None if absent/invalid."""
    config_path = _seed_dir(slug) / "config.json"
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


_SCENARIOS_FILENAME = "scenarios.json"


@lru_cache(maxsize=None)
def _load_scenarios_doc(slug: str) -> dict[str, Any] | None:
    """Read and parse ``seeds/<slug>/scenarios.json``; None if absent/invalid."""
    path = _seed_dir(slug) / _SCENARIOS_FILENAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_seed_scenarios(slug: str) -> list[dict[str, Any]]:
    """Return the seeded persona-scenarios for a template, or ``[]`` when none.

    Each entry is an ingestion-ready persona dict (``name`` / ``situation`` /
    ``outcome`` / ``scenario_name`` + a nested ``persona`` identity) consumed by
    ``alk_simulate_ingestion.provision_alk_sim_run_test(personas=...)``. The
    extra authoring fields (``use_case`` / ``tests`` / ``disposition`` /
    ``instruction`` / ``stance``) ride along and are preserved on the created
    scenario's ``metadata['persona']``.
    """
    if slug not in _OVERLAY:
        return []
    doc = _load_scenarios_doc(slug)
    if not isinstance(doc, dict):
        return []
    scenarios = doc.get("scenarios")
    if not isinstance(scenarios, list):
        return []
    return [entry for entry in scenarios if isinstance(entry, dict)]


def _summary(slug: str, overlay: dict[str, Any]) -> dict[str, Any] | None:
    """Build the UI-facing summary for one template, or None if unusable."""
    config = _load_config(slug)
    if config is None or not _seed_dir(slug).is_dir():
        return None

    workflow = config.get("workflow") or {}
    phases = workflow.get("agents") or []
    tools = config.get("tools") or []

    return {
        "slug": slug,
        "display_name": config.get("display_name") or slug,
        "description": config.get("description") or "",
        "channel": config.get("channel") or "voice",
        "direction": config.get("direction") or "inbound",
        "languages": config.get("languages") or [],
        "vertical": overlay["vertical"],
        "icon": overlay["icon"],
        "default_scenario_count": int(overlay["default_scenario_count"]),
        # Cheap structural signals that let the gallery hint at depth without
        # shipping the full workflow to the browser.
        "phase_count": len(phases),
        "tool_count": len(tools),
        # How many scenarios ship seeded with this agent (0 until authored).
        "seeded_scenario_count": len(load_seed_scenarios(slug)),
    }


def list_templates() -> list[dict[str, Any]]:
    """Return every usable template summary, in curated display order."""
    summaries = []
    for slug, overlay in sorted(_OVERLAY.items(), key=lambda kv: kv[1]["order"]):
        summary = _summary(slug, overlay)
        if summary is not None:
            summaries.append(summary)
    return summaries


def get_template(slug: str) -> dict[str, Any] | None:
    """Return one template summary by slug, or None when it is not exposed."""
    overlay = _OVERLAY.get(slug)
    if overlay is None:
        return None
    return _summary(slug, overlay)


def iter_template_files(slug: str) -> Iterator[tuple[str, bytes]]:
    """Yield ``(repo_relative_posix_path, content_bytes)`` for a template folder.

    Raises ``KeyError`` when the slug is not an exposed template so callers get
    a clear failure rather than an empty archive.
    """
    if slug not in _OVERLAY:
        raise KeyError(slug)
    root = _seed_dir(slug)
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        yield path.relative_to(root).as_posix(), path.read_bytes()
