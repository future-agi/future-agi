"""E2E encryption keys must be supplied at runtime, never committed literals."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "e2e/stack/docker-compose.e2e.yml"
STARTUP_TEST = ROOT / "deploy/tests/test_observed_catalog_startup.py"
FERNET_TOKEN = re.compile(rb"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}=(?![A-Za-z0-9_=-])")


class ComposeLoader(yaml.SafeLoader):
    """Understand Compose's sequence reset without resolving any interpolation."""


ComposeLoader.add_constructor("!reset", ComposeLoader.construct_sequence)


def test_compose_requires_runtime_fernet_key():
    document = yaml.load(COMPOSE.read_text(encoding="utf-8"), Loader=ComposeLoader)
    references = {"INTEGRATION_ENCRYPTION_KEY": [], "WEBHOOKS_ENCRYPTION_KEYS": []}
    for service in document["services"].values():
        environment = service.get("environment") or {}
        if isinstance(environment, list):
            environment = dict(item.partition("=")[::2] for item in environment)
        for name, checks in references.items():
            if name in environment:
                value = environment[name]
                checks.append(isinstance(value, str) and value.startswith("${" + name + ":?"))
    # Only booleans/counts reach diagnostics; never expose the actual values.
    assert len(references["INTEGRATION_ENCRYPTION_KEY"]) >= 2
    assert len(references["WEBHOOKS_ENCRYPTION_KEYS"]) >= 1
    assert all(all(checks) for checks in references.values()), (
        "Every key reference must require its matching runtime value"
    )


def test_no_committed_fernet_tokens():
    # Report only counts, even on the expected RED run.
    counts = [len(FERNET_TOKEN.findall(path.read_bytes())) for path in (COMPOSE, STARTUP_TEST)]
    assert counts == [0, 0], "Committed Fernet-shaped token counts must both be zero"
