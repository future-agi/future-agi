"""Curated starter agents ("templates") for the hosted RL-environment harness.

Each template is a self-contained LiveKit agent vendored under ``seeds/<slug>/``
(``agent.py`` + ``config.json`` + runtime scaffold). The catalog exposes their
metadata for the create UI, and packs a template's folder into a caller-scoped
source archive on demand so it flows through the normal create/preflight/run
pipeline exactly like an uploaded folder.
"""

from simulate.harness_templates.catalog import (
    get_template,
    iter_template_files,
    list_templates,
    load_seed_scenarios,
)

__all__ = [
    "get_template",
    "iter_template_files",
    "list_templates",
    "load_seed_scenarios",
]
