// Worst first: an area reads as its worst counted row.
const STATUS_ORDER = [
  "blocked",
  "stale",
  "untested",
  "partial",
  "unavailable",
  "supported",
];
const OPEN = new Set(["blocked", "stale", "untested"]);
const LIMIT = new Set(["partial", "unavailable"]);

const rank = (status) => {
  const at = STATUS_ORDER.indexOf(status);
  return at === -1 ? STATUS_ORDER.length : at;
};

// The pair check's state as the section words it.
const CHECK_STATES = {
  validating: "checking",
  failed: "failed",
  check_unavailable: "ours",
  retry: "retry",
  unavailable: "unavailable",
};

// The server refuses a run while the pair is checked (`pair_validating`) or after its check failed (`pair_failed`).
const HOLDS_RUN = new Set(["checking", "failed"]);

// A row nothing measures yet (browser, sub-agents) would keep its area "not checked" forever; it counts only once
// it affects a scenario.
const counts = (row) => row.measured || (row.affected?.count ?? 0) > 0;

// A fix someone makes on the agent: new credentials, or a new version that changes how a tool is served.
const AGENT_FIXES = new Set(["preflight"]);
export const isAgentFix = (fix) =>
  fix?.category === "agent_version" || AGENT_FIXES.has(fix?.type);

const areaLine = (area, rowsById) => {
  const counted = (area.rows || [])
    .map((id) => rowsById.get(id))
    .filter((row) => row && counts(row))
    .sort((a, b) => rank(a.status) - rank(b.status));
  const worst = counted[0];
  if (!worst) return { area: area.id, status: "supported" };
  return {
    area: area.id,
    status: worst.status,
    rowId: worst.id,
    reason: worst.reason,
    consequence: worst.consequence,
    // Null when nothing has been read for the row yet.
    checkedAt: worst.checked_at ?? null,
    affected: worst.affected?.count ?? 0,
    fix: worst.fix ?? null,
  };
};

/**
 * What the run dialog's Ready to run section shows, from a readiness report: what the check is doing, whether it
 * holds the run, the areas with something to fix, the stated limits and the areas that are fine.
 */
export function readinessView(report) {
  const validation = report?.validation || {};
  const state =
    CHECK_STATES[validation.state] ||
    (report?.pairing?.evidence === "none"
      ? "unchecked"
      : report?.answer?.kind) ||
    "unchecked";
  const rowsById = new Map((report?.rows || []).map((row) => [row.id, row]));
  const lines = (report?.areas || []).map((area) => areaLine(area, rowsById));
  const grading = report?.selection?.grading || {};
  return {
    state,
    stateReason: validation.error?.reason ?? null,
    // When the report was worked out: ties scenario names to its counts.
    computedAt: report?.computed_at ?? null,
    holdsRun: HOLDS_RUN.has(state),
    open: lines
      .filter((line) => OPEN.has(line.status))
      .sort((a, b) => rank(a.status) - rank(b.status)),
    limits: lines
      .filter((line) => LIMIT.has(line.status))
      .map(({ area, status, reason }) => ({ area, status, reason })),
    ready: lines
      .filter((line) => line.status === "supported")
      .map((line) => line.area),
    grading: {
      gradable: grading.gradable ?? 0,
      notGradable: grading.not_gradable?.length ?? 0,
    },
  };
}
