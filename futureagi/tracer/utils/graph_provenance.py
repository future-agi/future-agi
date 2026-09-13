"""Declared provenance of one Observe graph read, and the exactness it proves.

An Observe graph response carries two independent claims:

``query_complete``
    the read covered the whole requested window and published every point it
    was asked for; nothing was truncated by a budget.
``query_exact``
    the published values were computed from the latest physical state of every
    contributing span.

Only the provenances in :data:`EXACT_GRAPH_PROVENANCES` prove the second claim.
The two live ClickHouse read paths do not: ``materialized_rollup`` reads a
pre-aggregated hourly table, and ``bounded_candidates`` reads the full bounded
window without collapsing ReplacingMergeTree span versions, because that
collapse is too expensive on an interactive chart. Both are therefore complete
and inexact by construction, and a client that needs an exact series must wait
for the ``exact_snapshot`` refresh to publish one.

Keeping the pairing here — rather than writing ``query_provenance`` and
``query_exact`` out by hand at each read site — is what stops the two fields
from drifting apart.
"""

BOUNDED_CANDIDATES = "bounded_candidates"
EMPTY_WINDOW = "empty_window"
EXACT_SNAPSHOT = "exact_snapshot"
MATERIALIZED_ROLLUP = "materialized_rollup"
SERVER_READ_POLICY_UNAVAILABLE = "server_read_policy_unavailable"

#: Provenances whose values equal the latest physical state. ``empty_window``
#: qualifies because a window with no duration contributes no rows at all, so
#: the empty series is exact without any read having happened.
EXACT_GRAPH_PROVENANCES = frozenset({EMPTY_WINDOW, EXACT_SNAPSHOT})

#: Provenances that answer the whole window but do not prove latest state.
INEXACT_GRAPH_PROVENANCES = frozenset(
    {
        BOUNDED_CANDIDATES,
        MATERIALIZED_ROLLUP,
        SERVER_READ_POLICY_UNAVAILABLE,
    }
)

GRAPH_PROVENANCES = tuple(sorted(EXACT_GRAPH_PROVENANCES | INEXACT_GRAPH_PROVENANCES))


def graph_provenance_is_exact(provenance: str) -> bool:
    """Return whether ``provenance`` proves latest-physical-state values."""

    if provenance not in GRAPH_PROVENANCES:
        raise ValueError(f"unknown graph query provenance: {provenance!r}")
    return provenance in EXACT_GRAPH_PROVENANCES


def graph_provenance_metadata(provenance: str) -> dict[str, object]:
    """Return the response fields that declare one complete graph read."""

    return {
        "query_provenance": provenance,
        "query_exact": graph_provenance_is_exact(provenance),
    }
