import { READY_TO_RUN_COPY, RUN_PLAN_COPY } from "./readiness.constants";

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
// it affects a scenario. So does a block: one that blocks none of the picked scenarios stops nothing, unless a fix
// is working on it.
const counts = (row) =>
  row.status === "blocked" && row.affected && !row.fix_progress
    ? row.affected.count > 0
    : row.measured || (row.affected?.count ?? 0) > 0;

// A fix someone makes on the agent: new credentials, or a new version that changes how a tool is served.
const AGENT_FIXES = new Set(["preflight"]);
export const isAgentFix = (fix) =>
  fix?.category === "agent_version" || AGENT_FIXES.has(fix?.type);

const isFixed = (line) =>
  line.status === "supported" && line.progress?.state === "fixed";

const areaLine = (area, rowsById) => {
  const rows = (area.rows || []).map((id) => rowsById.get(id)).filter(Boolean);
  const [worst] = rows
    .filter(counts)
    .sort((a, b) => rank(a.status) - rank(b.status));
  if (!worst || worst.status === "supported") {
    // A supported area a fix just mended still reads "Fixed".
    const fixed = rows.find((row) => row.fix_progress?.state === "fixed");
    return {
      area: area.id,
      status: "supported",
      ...(fixed && {
        rowId: fixed.id,
        progress: fixed.fix_progress,
        // Only the builder chat fixes lines, so a fixed one stays in its group.
        group: "builder",
      }),
    };
  }
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
    // Who fixes it and how a fix of it stands; none of these come while Fix is off.
    group: worst.fix_group ?? null,
    progress: worst.fix_progress ?? null,
    action: worst.fix_action ?? null,
    result: worst.fix_result ?? null,
    waitingOnArea: rowsById.get(worst.fix_progress?.waiting_on)?.area ?? null,
  };
};

const byGroup = (lines) => {
  const groups = { needs_you: [], builder: [], recheck: [] };
  lines.forEach((line) => (groups[line.group] ?? groups.needs_you).push(line));
  return groups;
};

/**
 * What the run plan's Ready to run section shows, from a readiness report: what the check is doing, whether it
 * holds the run, the areas with something to fix (by who fixes them, once the server offers Fix), the stated limits
 * and the areas that are fine.
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
  const { requested = 0, grading } = report?.selection || {};
  // The server sends fix_groups only while Fix is on.
  const grouped = Boolean(report?.fix_groups);
  // In groups, a line nobody can fix and that blocks nothing (a Stop not seen yet) is a note beside the limits.
  const noted = (line) =>
    grouped &&
    (line.status === "stale" || line.status === "untested") &&
    !line.fix &&
    !line.group;
  const open = lines
    .filter((line) => OPEN.has(line.status) && !noted(line))
    .sort((a, b) => rank(a.status) - rank(b.status));
  return {
    state,
    stateReason: validation.error?.reason ?? null,
    // When the report was worked out: ties scenario names to its counts.
    computedAt: report?.computed_at ?? null,
    holdsRun: HOLDS_RUN.has(state),
    open,
    groups: grouped ? byGroup([...open, ...lines.filter(isFixed)]) : null,
    limits: lines
      .filter((line) => LIMIT.has(line.status) || noted(line))
      .map(({ area, status, reason }) => ({ area, status, reason })),
    ready: lines
      .filter(
        (line) => line.status === "supported" && !(grouped && isFixed(line)),
      )
      .map((line) => line.area),
    grading: {
      gradable: grading?.gradable ?? 0,
      // The report lists only some of the rest.
      notGradable: grading ? requested - grading.gradable : 0,
    },
  };
}

/**
 * What the builder chat says when a fix ends, and its button, from the run's outcome as the server reads it. Null
 * while the run is active.
 */
export function fixEndCard(fix) {
  const copy = RUN_PLAN_COPY.fixEnd;
  const outcome = fix?.outcome;
  if (!outcome) return null;
  const { kind, message } = outcome;
  if (kind === "unsaved") {
    return {
      // A stale draft can't be saved, so it never asks to save it.
      text:
        fix.error?.code === "draft_stale"
          ? RUN_PLAN_COPY.draftStale
          : [RUN_PLAN_COPY.draftFix, message].filter(Boolean).join(" "),
      action: copy.openPlan,
    };
  }
  if (kind === "could_not_fix") {
    return {
      text: [READY_TO_RUN_COPY.progress.could_not_fix, message]
        .filter(Boolean)
        .join(". "),
      action: copy.openPlan,
    };
  }
  return {
    text: message,
    action: kind === "fixed" ? copy.review : copy.openPlan,
  };
}
