"""Run readiness: what an agent, environment and suite pairing is shown to support.

Computed on read, never stored per pairing. Only an observed check makes a row
Supported.
"""

from simulate.services.harness_readiness.catalogue import (
    AREAS,
    CATALOG_VERSION,
    CATALOGUE,
    RULES_VERSION,
    Capability,
    Pairing,
)
from simulate.services.harness_readiness.evidence import (
    ACTION_CERTIFICATION,
    TOOL_CERTIFICATION,
    pair_bundle,
)
from simulate.services.harness_readiness.findings import (
    StructuralFinding,
    preflight_findings,
    structural_findings,
)
from simulate.services.harness_readiness.report import (
    ReadinessReport,
    readiness_report,
    recheck,
    resolve_pairing,
    run_snapshot,
)
from simulate.services.harness_readiness.selection import (
    BUCKETS,
    CasePage,
    SelectionReport,
    case_page,
    selection_report,
)

__all__ = [
    "ACTION_CERTIFICATION",
    "AREAS",
    "BUCKETS",
    "CATALOGUE",
    "CATALOG_VERSION",
    "RULES_VERSION",
    "TOOL_CERTIFICATION",
    "Capability",
    "CasePage",
    "Pairing",
    "ReadinessReport",
    "SelectionReport",
    "StructuralFinding",
    "case_page",
    "pair_bundle",
    "preflight_findings",
    "readiness_report",
    "recheck",
    "resolve_pairing",
    "run_snapshot",
    "selection_report",
    "structural_findings",
]
