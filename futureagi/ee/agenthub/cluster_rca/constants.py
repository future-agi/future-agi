"""Constants and enums for the cluster RCA agent."""

from enum import Enum


class Confidence(str, Enum):
    HIGH = "H"
    MEDIUM = "M"
    LOW = "L"


class FindingType(str, Enum):
    """Categorizes what kind of observation a finding captures."""

    FAILURE_MODE = "failure_mode"  # the common failure pattern across traces
    BEHAVIORAL_DELTA = "behavioral_delta"  # what's different vs passing traces
    DEPLOY_CORRELATION = "deploy_correlation"  # temporal link to a deploy
    OUTLIER_TRACE = "outlier_trace"  # one trace breaks the cluster pattern
    PATTERN_EVIDENCE = "pattern_evidence"  # supporting evidence for a known pattern


# Loop control
CLUSTER_RCA_MAX_TURNS = 18  # safety net, not a target — event-convergence concludes most runs ~8-10
CLUSTER_RCA_COMPACT_KEEP_RECENT = 6  # keep last N tool results in full
CLUSTER_RCA_WRAPUP_TURNS = 3  # nudge the model to conclude this many turns before the ceiling
CLUSTER_RCA_COST_CEILING_USD = 5.0  # spend tripwire — at the ceiling, stop + force a synthesis (normal runs ~cents)

# Event-triggered convergence — when the cluster collapses to one dominant value
# on >=2 dimensions, the cause is localized; nudge the model to conclude.
CLUSTER_RCA_DOMINANT_PCT = 90.0  # top bucket >= this % counts as "dominant"
CLUSTER_RCA_DOMINANT_MIN_TOTAL = 3  # ignore tiny aggregates (1/1=100% is not a pattern)
CLUSTER_RCA_CONVERGENCE_DIMS = 2  # this many dominant dimensions ⇒ converged
CLUSTER_RCA_CONVERGENCE_GRACE = 3  # turns allowed after convergence (to quote evidence) before a hard stop
