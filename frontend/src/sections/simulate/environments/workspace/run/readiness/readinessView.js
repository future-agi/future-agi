import { READY_TO_RUN_COPY, RUN_PLAN_COPY } from "./readiness.constants";
import { RUN_PLAN_PROMPTS_COPY } from "../runPlanPrompts.constants";

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

const CHECK_STATES = {
  validating: "checking",
  failed: "failed",
  check_unavailable: "ours",
  retry: "retry",
  unavailable: "unavailable",
};

// A row nothing measures yet (browser, sub-agents) would keep its area
// "not checked" forever; it counts only once it affects a scenario.
const counts = (row) => row.measured || (row.affected?.count ?? 0) > 0;

// Who fixes a row when the server doesn't say: a re-check alone, or the user.
const fallbackGroup = ({ fix }) => {
  const recheckOnly =
    (fix?.type === "recheck" && fix.recheck?.scope === "pair") ||
    fix?.type === "agent_tools_unread";
  return recheckOnly ? "recheck" : "needs_you";
};

const groupOf = (row) => row.fix_group ?? fallbackGroup(row);

const areaLine = (area, rowsById) => {
  const rows = (area.rows || []).map((id) => rowsById.get(id)).filter(Boolean);
  const counted = rows
    .filter(counts)
    .sort((a, b) => rank(a.status) - rank(b.status));
  const worst = counted[0];
  if (!worst || worst.status === "supported") {
    // A supported area a fix just mended still reads "Fixed".
    const fixed = rows.find((row) => row.fix_progress?.state === "fixed");
    if (fixed) {
      return {
        area: area.id,
        status: "supported",
        rowId: fixed.id,
        progress: fixed.fix_progress,
        group: groupOf(fixed),
      };
    }
  }
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
    group: groupOf(worst),
    progress: worst.fix_progress ?? null,
    waitingOnArea: rowsById.get(worst.fix_progress?.waiting_on)?.area ?? null,
  };
};

const isFixed = (line) =>
  line.status === "supported" && line.progress?.state === "fixed";

/**
 * The lines the run popup's Ready to run section shows, from a readiness
 * report: what the check is doing, whether Run is allowed, the counts, the
 * areas with something to fix, the stated limits and the areas that are fine.
 */
export function readinessView(report) {
  const validation = report?.validation || {};
  const answer = report?.answer?.kind;
  const state =
    CHECK_STATES[validation.state] ||
    (report?.pairing?.evidence === "none" ? "unchecked" : answer) ||
    "unchecked";
  const rowsById = new Map((report?.rows || []).map((row) => [row.id, row]));
  const lines = (report?.areas || []).map((area) => areaLine(area, rowsById));
  const selection = report?.selection || {};
  const notGradable = selection.grading?.not_gradable || [];
  const open = lines
    .filter((line) => OPEN.has(line.status))
    .sort((a, b) => rank(a.status) - rank(b.status));
  const groups = { needs_you: [], builder: [], recheck: [] };
  [...open, ...lines.filter(isFixed)].forEach((line) =>
    (groups[line.group] ?? groups.needs_you).push(line),
  );

  return {
    state,
    stateReason: validation.error?.reason ?? null,
    // When the report was worked out: ties scenario names to its counts.
    computedAt: report?.computed_at ?? null,
    canRun: state !== "checking" && state !== "failed",
    summary: {
      requested: selection.requested ?? 0,
      eligible: selection.eligible ?? 0,
      blocked: selection.blocked ?? 0,
      stale: selection.stale ?? 0,
      untested: selection.untested ?? 0,
    },
    open,
    // The server sends fix_groups only once it supports fixes.
    grouped: Boolean(report?.fix_groups),
    groups,
    limits: lines
      .filter((line) => LIMIT.has(line.status))
      .map(({ area, status, reason }) => ({ area, status, reason })),
    ready: lines
      .filter((line) => line.status === "supported" && !isFixed(line))
      .map((line) => line.area),
    grading: {
      gradable: selection.grading?.gradable ?? 0,
      notGradable: notGradable.length,
    },
  };
}

/**
 * What the builder chat says when a fix ends, and its button: how it ended,
 * line by line, and once saved, what the run plan still has open in `report`.
 * Null while that report loads. `draftsGone`: the drafts list has none left.
 */
export function fixEndCard(fix, report, draftsGone = false) {
  const copy = RUN_PLAN_COPY.fixEnd;
  const lines = fix.lines || [];
  const failed = lines.filter((line) => line.state === "could_not_fix");
  const couldNotFix = (reason) => ({
    text: [READY_TO_RUN_COPY.progress.could_not_fix, reason]
      .filter(Boolean)
      .join(". "),
    action: copy.openPlan,
  });
  if (lines.length && failed.length === lines.length) {
    return couldNotFix(failed[0].reason?.message ?? fix.error?.message);
  }
  if (fix.state === "done" && fix.draft) {
    // The draft holding the fix's changes was saved or discarded since.
    if (draftsGone) return { text: copy.draftGone, action: copy.openPlan };
    // A stale draft can't be saved, so it never asks to save it.
    if (fix.error?.code === "draft_stale") {
      return { text: RUN_PLAN_PROMPTS_COPY.draftStale, action: copy.openPlan };
    }
    return {
      text: [RUN_PLAN_PROMPTS_COPY.draftFix, fix.error?.message]
        .filter(Boolean)
        .join(" "),
      action: copy.openPlan,
    };
  }
  if (fix.state !== "done") return couldNotFix(fix.error?.message);
  // A report from before the fix ended still shows what it fixed.
  const before =
    fix.finished_at &&
    report?.computed_at &&
    new Date(report.computed_at) < new Date(fix.finished_at);
  if (!report || before) return null;
  const fixed = copy.fixed(
    lines.filter((line) => line.state === "fixed").length,
    lines.length,
  );
  // Blockers only, as the plan's header counts them.
  const open = readinessView(report).open.filter(
    (line) => line.status === "blocked",
  );
  if (!open.length) {
    return {
      text: failed.length ? `${fixed} ${copy.passed}` : copy.clear,
      action: copy.review,
    };
  }
  const labels = open.map(
    (line) => READY_TO_RUN_COPY.areas[line.area] || line.area,
  );
  return { text: `${fixed} ${copy.open(labels)}`, action: copy.openPlan };
}
