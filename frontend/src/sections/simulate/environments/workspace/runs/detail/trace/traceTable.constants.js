import { STATUS_META } from "../../runs.constants";

// Trace-table vocabulary and grouping logic, ported from the designer's
// TraceTable. Kept hex-free (colours resolve from BUILD_TONES / theme tokens in
// the components); this module is pure data + pure functions so the pickers,
// the table body and the tests read one source of truth.

/*
  Column picker vocabulary. `defaultOn` is what shows on first load, as in
  Vel's table: the run's own outcome (Run details), the persona and the system
  metrics read a run at a glance. The restated Scenario column, the scenario's
  other details and the interruption metrics start hidden and stay in the
  picker. The run's evaluations aren't listed here: they come from the calls
  API, and the picker offers each one on its own.
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
    defaultOn: false,
    width: 140,
    group: "System metrics",
  },
  {
    key: "aiInterruptions",
    label: "AI interruptions",
    defaultOn: false,
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

// Why a value turned red: the threshold, said plainly.
export const BAD_WHY = {
  csat: (v) =>
    `CSAT ${v} of 10. ${METRIC_THRESHOLDS.csat.bad} or below is an unhappy customer.`,
  turns: (v) =>
    `${v} turns. ${METRIC_THRESHOLDS.turns.bad} or more is a long conversation for one task.`,
  latency: (v) =>
    `${v}ms average reply. ${METRIC_THRESHOLDS.latency.bad}ms or slower feels laggy.`,
  tokens: (v) =>
    `${Number(v).toLocaleString()} tokens. ${METRIC_THRESHOLDS.tokens.bad.toLocaleString()} or more is among the heaviest.`,
};

/*
  Group-by axes in Vel's order, keyed by the `group_by` value sent: the
  Scenarios tab's axes (read from each call's authored scenario), the run's own
  status, the persona, and Vel's failure axes. `soon` marks an axis the API
  doesn't group by yet; it is still offered and sent, and a server that refuses
  it falls back to the default with a note. Vel's Member axis is left out: it
  only applies to an agent made of several agents, which runs don't record.
*/
export const DEFAULT_GROUP_BY = "goal";
export const GROUPINGS = [
  { id: "goal", label: "Use case", icon: "solar:target-linear" },
  { id: "sub_goal", label: "Sub-goal", icon: "solar:map-linear" },
  { id: "persona", label: "Persona", icon: "solar:user-id-linear" },
  {
    id: "rule",
    label: "Rule broken",
    icon: "solar:shield-cross-linear",
    soon: "Grouping by the rule a call broke",
  },
  {
    id: "failure_sub_goal",
    label: "Failure sub-goal",
    icon: "solar:close-circle-linear",
    soon: "Grouping by the sub-goal a call failed on",
  },
  {
    id: "pattern",
    label: "Failure pattern",
    icon: "solar:danger-triangle-linear",
    soon: "Grouping by failure pattern",
  },
  { id: "status", label: "Status", icon: "solar:check-circle-linear" },
  {
    id: "cause",
    label: "Verdict cause",
    icon: "solar:tag-linear",
    soon: "Grouping by verdict cause",
  },
  { id: "accent", label: "Accent", icon: "solar:soundwave-linear" },
  { id: "age", label: "Age", icon: "solar:user-rounded-linear" },
  { id: "attack", label: "Attack", icon: "solar:shield-warning-linear" },
  { id: "task", label: "Task", icon: "solar:checklist-minimalistic-linear" },
  { id: "", label: "No grouping", icon: "solar:list-linear" },
];

// An axis whose groups can split one scenario's repeats apart: the call's own
// outcome. Its group counts are calls, never scenarios.
export const SPLITS_TRIALS = new Set(["status"]);

// A call's outcome, as its row and the Filter panel name it, keyed by the
// server's status value.
export const OUTCOME_LABELS = {
  queued: "Queued",
  in_progress: "In progress",
  failed: "Failed",
  error: "Error",
  inconclusive: "Inconclusive",
  passed: "Passed",
};

/*
  The status chips, in Vel's names and order. `id` is the server's status
  value. Flaky and Test invalid have no data yet, so they count 0 and stay
  hidden. Queued and In progress only show while the run is live. All, Failing
  and Passing always show; any other chip hides at zero.
*/
export const STATUS_CHIPS = [
  { id: "all", label: "All", tone: null, about: "" },
  {
    id: "queued",
    label: "Queued",
    tone: null,
    liveOnly: true,
    about: "Waiting to start.",
  },
  {
    id: "in_progress",
    label: "In progress",
    tone: "blue",
    liveOnly: true,
    about: "Running now.",
  },
  {
    id: "failed",
    label: "Failing",
    tone: "red",
    always: true,
    about: "The agent didn't do what the scenario asked.",
  },
  {
    id: "flaky",
    label: "Flaky",
    tone: "amber",
    about:
      "Passed some repeats and failed others. The agent isn't reliable here.",
  },
  {
    id: "inconclusive",
    label: "Inconclusive",
    tone: null,
    about:
      "Not enough evidence to grade. Never counted as a pass; left out of pass rates.",
  },
  {
    id: "test_invalid",
    label: "Test invalid",
    tone: null,
    about:
      "The test broke, not the agent. Counts neither for nor against the agent.",
  },
  {
    id: "error",
    label: "Error",
    tone: "amber",
    about: "Our infrastructure or the connection failed.",
  },
  {
    id: "passed",
    label: "Passing",
    tone: "green",
    always: true,
    about: "Did what the scenario asked.",
  },
];

export const neutralCheckboxSx = {
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
  "&.MuiCheckbox-indeterminate": { color: "text.primary" },
};

export const TOOLBAR_COMPACT = "@container (max-width: 1240px)";

export const toolbarButtonSx = {
  typography: "s2",
  fontWeight: "fontWeightBold",
  textTransform: "none",
  height: 28,
  minWidth: 0,
  px: 1,
  flexShrink: 0,
  whiteSpace: "nowrap",
  color: "text.primary",
  borderColor: "divider",
  "&:hover": { borderColor: "text.disabled", bgcolor: "transparent" },
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
  fontWeight: "fontWeightBold",
  color: "text.subtitle",
  textTransform: "uppercase",
  letterSpacing: 0.4,
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

// Call outcomes share their labels with the filters.
export function runOutcome(status) {
  const meta = STATUS_META[status] || STATUS_META.unmeasured;
  return {
    label: OUTCOME_LABELS[status] || OUTCOME_LABELS.inconclusive,
    color: meta.color,
  };
}
