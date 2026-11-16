// Scenarios-tab copy and grouping helpers.
//
// deriveUseCase / groupScenarios are copied verbatim from the designer
// ScenariosStep (REF lines 522-571). The grouping is what both the list and the
// table read: rows are bucketed by the axis the reader chose — the task the
// agent is asked to do (Goal), the caller (Persona), or the first step
// (Sub-goal) — not by the derivation kind they fell out of.

import { subTasksFor } from "src/api/simulate-environments/_fixtures/contract";

// Table row height, as the Display menu names it: how many lines a text
// cell shows and how many sub-goals are listed. 0 lines and Infinity show all.
export const ROW_HEIGHTS = {
  Short: { lines: 3, subTasks: 3 },
  Medium: { lines: 5, subTasks: 5 },
  Large: { lines: 8, subTasks: 8 },
  "Extra large": { lines: 0, subTasks: Infinity },
};

// Toolbar buttons are the theme's small secondary buttons; this only keeps a
// label on one line and the button from shrinking.
export const scenarioToolbarButtonSx = { whiteSpace: "nowrap", flexShrink: 0 };

export const SCENARIOS_COPY = {
  searchPlaceholder: "Search scenarios by name, task or use case…",
  addLabel: "Add scenarios",
  addShortLabel: "Add",
  editLabel: "Edit scenario",
  openRow: (name) => `Open ${name}`,
  removeLabel: "Remove from this environment",
  filterLabel: "Filter",
  filterTitle: "Filter by use case",
  clearLabel: "Clear",
  rowHeight: "Row height",
  groupByLabel: "Group by",
  displayLabel: "Display",
  viewLabel: "View",
  hideGroup: "Hide this group",
  showAll: "Show all",
  allHidden: "Every group is hidden. Click Show all to bring them back.",
  noMatch: "No scenarios match your filters.",
  emptyTitle: "No scenarios yet",
  emptyBody:
    "Scenarios are normally derived from your agent when the environment is built.",
  critical: "Critical: a failure here is a release blocker",
  proofCounts: ({
    proved = 0,
    kept_as_written: kept = 0,
    not_proved: notProved = 0,
  }) => `${proved} proved · ${kept} kept as written · ${notProved} not proved`,
  lifecycle: {
    draft: "Draft",
    validated: "Validated",
    active: "Active",
    kept_as_written: "Kept as written",
    archived: "Archived",
  },
  legacyTooltip:
    "Validated before checks were recorded. Validate again to confirm.",
  keptAsWritten: "Kept as written",
  notMeasured: "Not measured at run time",
  couldNotApply: "Could not apply",
  discardChange: "Discard unsaved scenario changes",
  lifecycleFilterLabel: "Lifecycle",
  versionsLabel: "Versions",
  versionsDefault: "Saved (default)",
  versionPair: (suite, environment, candidate) =>
    `Suite ${suite} on environment ${environment}${candidate ? " (candidate)" : ""}`,
  versionsInView:
    "Showing these versions. Cases proved here read Validated; they read Active once these versions are saved.",
  recordHint: "This case presumes a record the world does not hold.",
  recordTable: "Table",
  recordPick: "Record",
  useRealRecord: "Use a real record",
  approveSynthetic: "Approve as a synthetic record",
  syntheticId: "Synthetic record id",
  realRecordMessage: (key, table, id) =>
    `Change scenario ${key} to use ${table} record ${id} instead of the record it presumes that the world does not hold.`,
  syntheticQuote: (user) => `Synthetic record approved by ${user}`,
  activate: "Activate",
  activateHint: "Activating saves these versions. It does not start a run.",
  activateNothing: "Nothing to save: these versions are not unsaved changes.",
  lifecycleFilters: [
    { value: "", label: "All" },
    { value: "draft", label: "Draft" },
    { value: "validated", label: "Validated" },
    { value: "active", label: "Active" },
    { value: "kept_as_written", label: "Kept as written" },
    { value: "archived", label: "Archived" },
    { value: "source_changed", label: "Source changed" },
  ],
  validate: "Validate all against the environment",
  validateOne: "Validate scenario against the environment",
  validateDrafts: "Validate with the unsaved changes",
  validating: "Validating… (up to 45 min)",
  validationNoResults:
    "Validation finished but recorded no results; try again.",
  archive: "Archive",
  restore: "Restore",
  archiveTitle: "Archive this scenario?",
  archiveReason: "Reason (optional)",
  archived: "Archived. It no longer runs.",
  restored: "Restored as a draft. Validate and save to run it.",
  restoredLive: "Restored. It runs again.",
  draftChanged: "The environment was edited elsewhere; reload and try again.",
  savedAsDraft: (n) => `${n} cases saved as Draft; validate them to run them`,
  noSources: "No source recorded (written before sources were required).",
  assumption: "Assumption",
  sourcesTitle: "Sources",
  statusTitle: "Status",
  controlsTitle: "Negative controls",
  revisionsTitle: "Revisions",
  controlResult: (control) =>
    control.assumed?.length
      ? "Not run"
      : control.failed === false
        ? "Passed"
        : control.failed
          ? "Failed"
          : "Not run",
  sourceChanged: (version) => `Source changed in environment v${version}`,
  compare: "Compare",
  citedRule: "a cited rule",
  compareTitle: (from, to) =>
    `What changed between environment v${from} and v${to}`,
  compareRule: "Rule",
  compareBefore: (version) => `Before (v${version})`,
  compareAfter: (version) => `After (v${version})`,
  compareMissing: "Not in this version.",
  compareLoading: "Reading both versions…",
  compareFailed: "Couldn't read the change. Try again.",
  compareCitedBy: (keys) =>
    `Cited by ${keys.length === 1 ? "1 case" : `${keys.length} cases`}: ${keys.join(", ")}`,
  addMissingCases: "Add missing cases",
  uncoveredTitle: "Planned levels no scenario covers yet",
  requestedTitle: "Asked for, not written yet",
  requestedGuidance: (lines) => `Write these cases you were asked for and have not written yet: ${lines.join("; ")}`,
  workflowTitle: "Release checks per workflow",
  workflowColumn: "Workflow",
  workflowKinds: { success: "Success", refusal: "Refusal", wrong_result: "Wrong result" },
  workflowMissing: "Missing",
  workflowGuidance: (kind, workflow) =>
    `Write the missing ${kind.replace("_", " ")} case for the workflow "${workflow}".`,
};

// The version pairs the Scenarios tab can show besides the saved one: each candidate pair Validate
// built, and each other active suite or environment version on the other's default.
const versionName = (version) => version?.label || `v${String(version?.id ?? "").slice(0, 6)}`;
export const versionPairs = (versions) => {
  if (!versions || versions.empty) return [];
  const suites = versions.suite ?? [];
  const environments = versions.environment ?? [];
  const suiteDefault = versions.defaults?.suite;
  const environmentDefault = versions.defaults?.environment;
  const pairs = [];
  const add = (suite, environment) => {
    if (!suite || !environment || (suite.isDefault && environment.isDefault)) return;
    const value = `${suite.id}:${environment.id}`;
    if (pairs.some((one) => one.value === value)) return;
    const candidate = suite.status === "candidate" || environment.status === "candidate";
    pairs.push({
      value,
      suiteVersionId: suite.id,
      environmentVersionId: environment.id,
      label: SCENARIOS_COPY.versionPair(versionName(suite), versionName(environment), candidate),
    });
  };
  const candidateSuite = suites.find((one) => one.status === "candidate");
  const candidateEnvironment = environments.find((one) => one.status === "candidate");
  if (candidateSuite || candidateEnvironment) {
    add(candidateSuite ?? suiteDefault, candidateEnvironment ?? environmentDefault);
  }
  suites
    .filter((one) => !one.isDefault && ["active", "candidate"].includes(one.status))
    .forEach((one) => add(one, environmentDefault));
  environments
    .filter((one) => !one.isDefault && ["active", "candidate"].includes(one.status))
    .forEach((one) => add(suiteDefault, one));
  return pairs;
};

// Badge role per lifecycle state, from the design system's status roles.
export const LIFECYCLE_ROLES = {
  draft: "neutral",
  validated: "info",
  active: "success",
  kept_as_written: "warning",
  archived: "neutral",
};
// A warning on a case that runs and is proved: a check reads evidence this agent does not give.
export const RUN_TIME_WARNINGS = ["evidence_not_captured"];

// A row the unsaved changes add or change; an added one can't be picked for a
// run yet.
export const hasUnsavedChange = (row) =>
  row?.draftChange === "added" || row?.draftChange === "changed";
export const isUnsavedAddition = (row) => row?.draftChange === "added";
// The ids a header checkbox selects: every row but the unsaved additions.
export const selectableRowIds = (rows) =>
  rows
    .filter((r) => !isUnsavedAddition(r))
    .map((r) => r.id)
    .filter(Boolean);
// The id of an added row's visible "Save to run" reason, for its checkbox.
export const unsavedReasonId = (row) => `unsaved-${row.id}`;
// An added row's checkbox: off, and described by its "Save to run" reason.
export const unsavedCheckboxProps = (row) =>
  isUnsavedAddition(row)
    ? {
        disabled: true,
        inputProps: { "aria-describedby": unsavedReasonId(row) },
      }
    : {};

// The three axes a reader scans scenarios by. The trace table on the run view
// uses the same three, so a scenarios-tab "Persona" bucket matches the trace
// table's "Persona" bucket after a run.
export const SCENARIO_GROUPINGS = [
  { id: "goal", label: "Goal", icon: "solar:target-linear" },
  { id: "persona", label: "Persona", icon: "solar:user-rounded-linear" },
  { id: "subgoal", label: "Sub-goal", icon: "solar:map-linear" },
];

const humanize = (s = "") =>
  s
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();

// The scenario carries its own use-case sentence; the group key is a slug of
// that sentence so scenarios with matching text land together. Rows that
// predate the field fall back to the id-based derivation.
export const deriveUseCase = (row) => {
  if (row?.useCase) {
    const slug = row.useCase
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
    return { id: slug, label: row.useCase };
  }
  const id = row?.id || "";
  const title = row?.title || "";
  if (id.includes("-core-")) {
    const m = title.match(/(?:using|needing|landing on)\s+([\w_-]+)/i);
    if (m) return { id: `tool:${m[1].toLowerCase()}`, label: humanize(m[1]) };
    return { id: "tool:other", label: "Tool use" };
  }
  if (id.includes("-rule-")) {
    const short = title.split(/[.:]/)[0].slice(0, 42).trim();
    return {
      id: `rule:${short.toLowerCase()}`,
      label: short || "Rule enforcement",
    };
  }
  if (id.includes("-trap-")) {
    const table = title.split(":")[0].trim();
    if (table)
      return {
        id: `trap:${table.toLowerCase()}`,
        label: `${humanize(table)} data`,
      };
    return { id: "trap:other", label: "Data traps" };
  }
  if (id.includes("-adversarial-"))
    return { id: `adv:${title.toLowerCase()}`, label: title || "Adversarial" };
  if (id.includes("-edge-"))
    return { id: `edge:${title.toLowerCase()}`, label: title || "Edge case" };
  return { id: "other", label: "Other" };
};

// Group key + label for one row, in a given mode. Goal reuses the use-case
// derivation; persona reads the caller name; sub-goal reads the first sub-task.
// IDs are dimension-prefixed so a "persona:polite" id means nothing under Goal
// grouping — switching the axis clears any hidden set keyed by the old ids.
export const groupKeyOf = (row, mode, env) => {
  if (mode === "persona") {
    const name = row?.persona?.name;
    if (!name) return { id: "persona:none", label: "No persona" };
    return { id: `persona:${name.toLowerCase()}`, label: name };
  }
  if (mode === "subgoal") {
    const subs = subTasksFor(row, env);
    const first = subs?.[0]?.label;
    if (!first) return { id: "subgoal:none", label: "No sub-goals" };
    const slug = first
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
    return { id: `subgoal:${slug}`, label: first };
  }
  return deriveUseCase(row);
};

export const groupScenarios = (rows, mode = "goal", env) => {
  const buckets = new Map();
  (rows || []).forEach((r) => {
    const key = groupKeyOf(r, mode, env);
    if (!buckets.has(key.id))
      buckets.set(key.id, { id: key.id, label: key.label, rows: [] });
    buckets.get(key.id).rows.push(r);
  });
  // Bigger groups first — the tail of one-off adversarial/edge titles shouldn't
  // push the meaty groups out of view.
  return [...buckets.values()].sort((a, b) => b.rows.length - a.rows.length);
};
