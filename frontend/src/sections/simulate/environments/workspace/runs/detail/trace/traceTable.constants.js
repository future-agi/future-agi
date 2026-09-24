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
  {
    key: "callDetails",
    label: "Run details",
    defaultOn: true,
    width: 200,
    group: "Run details",
  },
  {
    key: "persona",
    label: "Persona",
    defaultOn: true,
    width: 200,
    group: "Scenario details",
  },
  {
    key: "scenario",
    label: "Scenario",
    defaultOn: false,
    width: 420,
    group: "Scenario details",
  },
  {
    key: "idealOutcome",
    label: "Ideal outcome",
    defaultOn: false,
    width: 420,
    group: "Scenario details",
  },
  {
    key: "conversationBranch",
    label: "Conversation branch",
    defaultOn: false,
    width: 320,
    group: "Scenario details",
  },
  {
    key: "csat",
    label: "CSAT",
    defaultOn: true,
    width: 84,
    group: "System metrics",
  },
  {
    key: "turns",
    label: "Turns",
    defaultOn: true,
    width: 92,
    group: "System metrics",
  },
  {
    key: "latency",
    label: "Latency",
    defaultOn: true,
    width: 96,
    group: "System metrics",
  },
  {
    key: "tokens",
    label: "Tokens",
    defaultOn: true,
    width: 96,
    group: "System metrics",
  },
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
  Group-by axes backed by native run fields. Persona remains available as a
  column, but is intentionally not a grouping or filtering axis.
*/
export const GROUPINGS = [
  { id: "useCase", label: "Goal", icon: "solar:target-linear" },
  { id: "status", label: "Status", icon: "solar:check-circle-linear" },
];

export const STATUS_CHIPS = [
  { id: "all", label: "All", tone: null },
  { id: "failing", label: "Failing", tone: "red" },
  { id: "errored", label: "Errored", tone: "amber" },
  { id: "inconclusive", label: "Inconclusive", tone: null },
  { id: "passing", label: "Passing", tone: "green" },
];

export const neutralCheckboxSx = {
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
  "&.MuiCheckbox-indeterminate": { color: "text.primary" },
};

// Shared cell sx. A hairline left border between columns and a bottom divider per
// row give the table its grid without a heavy outline.
export const headCellSx = {
  typography: "s2",
  fontWeight: "fontWeightMedium",
  color: "text.secondary",
  whiteSpace: "nowrap",
  bgcolor: "background.paper",
  height: 44,
  py: 0,
  borderBottom: "1px solid",
  borderColor: "divider",
  "&:not(:first-of-type)": { borderLeft: "1px solid", borderColor: "divider" },
};
export const numCellSx = {
  verticalAlign: "top",
  py: 1.5,
  typography: "s2",
  color: "text.secondary",
  fontVariantNumeric: "tabular-nums",
  borderBottom: "1px solid",
  borderColor: "divider",
  "&:not(:first-of-type)": { borderLeft: "1px solid", borderColor: "divider" },
};
export const bodyCellSx = {
  verticalAlign: "top",
  py: 1.5,
  borderBottom: "1px solid",
  borderColor: "divider",
  "&:not(:first-of-type)": { borderLeft: "1px solid", borderColor: "divider" },
};

// Outcome as colour + label so a failure is scannable at the row level. Reuses
// the run status colours; the `error` label is normalised to "Errored" to match
// the group/filter vocabulary.
export function runOutcome(status) {
  const meta = STATUS_META[status] || STATUS_META.unmeasured;
  return {
    label: status === "error" ? "Errored" : meta.label,
    color: meta.color,
  };
}
