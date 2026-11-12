import { STATUS_META } from "../../runs.constants";

// Trace-table vocabulary and grouping logic, ported from the designer's
// TraceTable. Kept colour-free (colours are theme tokens named by status role,
// resolved in the components); this module is pure data + pure functions so the pickers,
// the table body and the tests read one source of truth.

/*
  Column picker vocabulary. `defaultOn` is what shows on first load: the run's
  own outcome (Run details), the differentiating persona and the system metrics
  read a run at a glance; the restated Scenario column starts hidden. The run's
  evaluations aren't listed here: they come from the calls API, and the picker
  offers each one on its own.
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
    defaultOn: true,
    width: 420,
    group: "Scenario details",
  },
  {
    key: "situation",
    label: "Situation",
    defaultOn: false,
    width: 420,
    group: "Scenario details",
  },
  {
    key: "subGoals",
    label: "Sub-goals",
    defaultOn: false,
    width: 320,
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
    key: "stopLatency",
    label: "Stop latency",
    defaultOn: true,
    width: 140,
    group: "System metrics",
  },
  {
    key: "aiInterruptions",
    label: "AI interruptions",
    defaultOn: true,
    width: 150,
    group: "System metrics",
  },
  {
    key: "tokens",
    label: "Tokens",
    defaultOn: true,
    width: 96,
    group: "System metrics",
  },
  {
    key: "subGoalEvals",
    label: "Sub-goal Results",
    defaultOn: false,
    group: "Sub-goal Results",
  },
];

// Columns a chat run has no data for: interruptions only happen on a voice call.
export const VOICE_ONLY_COLUMNS = new Set(["aiInterruptions", "stopLatency"]);

// Every group closed: the table's starting state, and Collapse all. Expand all
// sets `all`; `expanded` holds the labels opened.
export const CLOSED_GROUP_VIEW = { all: false, expanded: new Set() };

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
  Group-by axes, keyed by the server's `group_by` value: the Scenarios tab's
  axes (read from each call's authored scenario) plus the run's own status.
*/
export const GROUPINGS = [
  { id: "goal", label: "Use case", icon: "solar:target-linear" },
  { id: "sub_goal", label: "Sub-goal", icon: "solar:map-linear" },
  { id: "accent", label: "Accent", icon: "solar:soundwave-linear" },
  { id: "age", label: "Age", icon: "solar:user-rounded-linear" },
  { id: "attack", label: "Attack", icon: "solar:shield-warning-linear" },
  { id: "task", label: "Task", icon: "solar:checklist-minimalistic-linear" },
  { id: "status", label: "Status", icon: "solar:check-circle-linear" },
  { id: "", label: "No grouping", icon: "solar:list-linear" },
];

export const STATUS_CHIPS = [
  { id: "all", label: "All", role: null },
  { id: "queued", label: "Queued", role: null },
  { id: "in_progress", label: "In progress", role: "info" },
  { id: "failed", label: "Failed", role: "danger" },
  { id: "error", label: "Error", role: "warning" },
  { id: "inconclusive", label: "Inconclusive", role: null },
  { id: "passed", label: "Passed", role: "success" },
];

export const neutralCheckboxSx = {
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
  "&.MuiCheckbox-indeterminate": { color: "text.primary" },
};

export const TOOLBAR_COMPACT = "@container (max-width: 1240px)";

export const toolbarButtonSx = {
  minWidth: 0,
  flexShrink: 0,
  whiteSpace: "nowrap",
};

export const compactHiddenSx = { [TOOLBAR_COMPACT]: { display: "none" } };

// The head row stays pinned while the calls scroll and the group rows pin just
// below it, so they need its height. Both measured in the browser.
export const HEAD_ROW_PX = 44;
export const GROUP_ROW_PX = 57;
// The band naming each column group, pinned above the head row.
export const GROUP_BAND_PX = 28;

// The checkbox column stays on the left edge while the table scrolls sideways.
// Opaque, so the cells scrolling under it don't show through; each row sets
// its own z-index (head and band above group rows, group rows above calls).
export const pinnedLeftSx = {
  position: "sticky",
  left: 0,
  bgcolor: "background.paper",
};

export const bandCellSx = {
  typography: "s3",
  fontWeight: 500,
  color: "text.subtitle",
  whiteSpace: "nowrap",
  bgcolor: "background.paper",
  height: GROUP_BAND_PX,
  position: "sticky",
  top: 0,
  zIndex: 3,
  py: 0,
  borderBottom: "1px solid",
  borderColor: "divider",
  "&:not(:first-of-type)": { borderLeft: "1px solid", borderColor: "divider" },
};

// Shared cell sx. A hairline left border between columns and a bottom divider per
// row give the table its grid without a heavy outline.
export const headCellSx = {
  typography: "s2",
  fontWeight: "fontWeightMedium",
  color: "text.secondary",
  whiteSpace: "nowrap",
  bgcolor: "background.paper",
  height: HEAD_ROW_PX,
  position: "sticky",
  top: 0,
  zIndex: 3,
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

export const OUTCOME_LABELS = Object.fromEntries(
  STATUS_CHIPS.filter((chip) => chip.id !== "all").map((chip) => [
    chip.id,
    chip.label,
  ]),
);

// Call outcomes share their labels with the filters. `role` is the status role
// (render it with StatusBadge); `color` is the same role as a text token.
export function runOutcome(status) {
  const meta = STATUS_META[status] || STATUS_META.unmeasured;
  const chip = STATUS_CHIPS.find((c) => c.id === status);
  const role = chip?.role ?? meta.role;
  return {
    label: OUTCOME_LABELS[status] || OUTCOME_LABELS.inconclusive,
    role,
    color: `status.${role}.fg`,
  };
}
