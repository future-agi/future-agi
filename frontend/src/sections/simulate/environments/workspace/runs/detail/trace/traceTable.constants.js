import { STATUS_META } from "../../runs.constants";

// Trace-table vocabulary and grouping logic, ported from the designer's
// TraceTable. Kept hex-free (colours resolve from BUILD_TONES / theme tokens in
// the components); this module is pure data + pure functions so the pickers,
// the table body and the tests read one source of truth.

/*
  Column picker vocabulary. `defaultOn` is what shows on first load: the run's
  own outcome (Run details), the differentiating persona and the system metrics
  read a run at a glance; the restated Scenario column starts hidden. `evals` is
  a single toggle for the whole (data-driven) evaluation group.
*/
export const TRACE_COLUMNS = [
  { key: "callDetails", label: "Run details", defaultOn: true, width: 200, group: "Run details" },
  { key: "persona", label: "Persona", defaultOn: true, width: 200, group: "Scenario details" },
  { key: "scenario", label: "Scenario", defaultOn: false, width: 300, group: "Scenario details" },
  { key: "csat", label: "CSAT", defaultOn: true, width: 84, group: "System metrics" },
  { key: "turns", label: "Turns", defaultOn: true, width: 92, group: "System metrics" },
  { key: "latency", label: "Latency", defaultOn: true, width: 96, group: "System metrics" },
  { key: "tokens", label: "Tokens", defaultOn: true, width: 96, group: "System metrics" },
  { key: "evals", label: "Evaluations", defaultOn: true, group: "Evaluations" },
];

export const defaultTraceColumns = () =>
  new Set(TRACE_COLUMNS.filter((c) => c.defaultOn).map((c) => c.key));

/*
  Only truly bad values get called out so red stays rare and meaningful. CSAT is
  on the 0–10 scale (bad ≤ 4); the rest are highIsBad thresholds pitched at the
  worst ~10–15% of each metric.
*/
export const METRIC_THRESHOLDS = {
  csat: { direction: "lowIsBad", bad: 4 },
  turns: { direction: "highIsBad", bad: 12 },
  latency: { direction: "highIsBad", bad: 550 },
  tokens: { direction: "highIsBad", bad: 6000 },
};

export const isBad = (metric, value) => {
  const spec = METRIC_THRESHOLDS[metric];
  if (!spec || value == null || Number.isNaN(value)) return false;
  return spec.direction === "lowIsBad" ? value <= spec.bad : value >= spec.bad;
};

/*
  Group-by axes. Goal/Persona/Status read real fields with no caveat. The two
  failure views (`subGoal`, `pattern`) are `sample: true` — the executions
  payload has no failure-taxonomy field, so they are derived from the run's eval
  failures and flagged in the picker.
*/
export const GROUPINGS = [
  { id: "useCase", label: "Goal", icon: "solar:target-linear" },
  { id: "persona", label: "Persona", icon: "solar:user-rounded-linear" },
  { id: "status", label: "Status", icon: "solar:check-circle-linear" },
  { id: "subGoal", label: "Failure sub-goal", icon: "solar:map-linear", sample: true },
  { id: "pattern", label: "Failure pattern", icon: "solar:danger-triangle-linear", sample: true },
];

// Every task lands in exactly one bucket per mode, or null when it has no bucket
// (a passed task under a failure view drops out entirely).
export function groupOfTask(t, mode) {
  if (mode === "persona") return t.persona || "No persona";
  if (mode === "status") {
    if (t.status === "passed") return "Passed";
    if (t.status === "unmeasured") return "Not measured";
    if (t.status === "error") return "Errored";
    return "Failed";
  }
  if (mode === "pattern") {
    if (t.status === "passed" || t.status === "unmeasured") return null;
    if (t.status === "error") return "Errored";
    if ((t.evalResults || []).some((r) => r.passed === false)) return "Evaluation failed";
    return "Other failure";
  }
  if (mode === "subGoal") {
    if (t.status === "passed" || t.status === "unmeasured") return null;
    const failed = (t.evalResults || []).find((r) => r.passed === false);
    return failed ? failed.name : "Unmeasured objective";
  }
  // useCase (Goal) and the fallback: the scenario is the real, human axis.
  return t.scenario || "Other";
}

export const GROUP_SORT_ORDER = {
  pattern: ["Errored", "Evaluation failed", "Other failure"],
  status: ["Failed", "Errored", "Not measured", "Passed"],
};

// Map a task's status to a chip bucket. An errored call is a measured failure
// (it reached a "this went wrong" verdict) → grouped with Failing; only
// genuinely unmeasured calls are Inconclusive. No real flaky feed, so `mixed`
// stays empty.
export function statusBucket(t) {
  if (t.status === "passed") return "passing";
  if (t.status === "flaky") return "mixed";
  if (t.status === "unmeasured") return "inconclusive";
  return "failing";
}

export const STATUS_CHIPS = [
  { id: "all", label: "All", tone: null },
  { id: "failing", label: "Failing", tone: "red" },
  { id: "mixed", label: "Mixed", tone: "amber" },
  { id: "inconclusive", label: "Inconclusive", tone: null },
  { id: "passing", label: "Passing", tone: "green" },
];

// The FilterPanel attribute vocabulary (client-side over the loaded rows).
export const STATUS_FILTER_LABELS = ["Passed", "Failed", "Errored", "Not measured"];
export const PATTERN_FILTER_LABELS = ["Errored", "Evaluation failed", "Other failure"];

export const statusFilterLabel = (t) => {
  if (t.status === "passed") return "Passed";
  if (t.status === "unmeasured") return "Not measured";
  if (t.status === "error") return "Errored";
  return "Failed";
};

export const neutralCheckboxSx = {
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
  "&.MuiCheckbox-indeterminate": { color: "text.primary" },
};

// Shared cell sx. A hairline left border between columns and a bottom divider per
// row give the table its grid without a heavy outline.
export const headCellSx = {
  typography: "s2", fontWeight: "fontWeightMedium", color: "text.secondary",
  whiteSpace: "nowrap", bgcolor: "background.paper", height: 44, py: 0,
  borderBottom: "1px solid", borderColor: "divider",
  "&:not(:first-of-type)": { borderLeft: "1px solid", borderColor: "divider" },
};
export const numCellSx = {
  verticalAlign: "top", py: 1.5,
  typography: "s2", color: "text.secondary", fontVariantNumeric: "tabular-nums",
  borderBottom: "1px solid", borderColor: "divider",
  "&:not(:first-of-type)": { borderLeft: "1px solid", borderColor: "divider" },
};
export const bodyCellSx = {
  verticalAlign: "top", py: 1.5,
  borderBottom: "1px solid", borderColor: "divider",
  "&:not(:first-of-type)": { borderLeft: "1px solid", borderColor: "divider" },
};
export const checkCellSx = {
  width: 48, p: 0, pl: 1.25, verticalAlign: "middle",
  borderBottom: "1px solid", borderColor: "divider",
};

// Outcome as colour + label so a failure is scannable at the row level. Reuses
// the run status colours; the `error` label is normalised to "Errored" to match
// the group/filter vocabulary.
export function runOutcome(status) {
  const meta = STATUS_META[status] || STATUS_META.unmeasured;
  return { label: status === "error" ? "Errored" : meta.label, color: meta.color };
}
