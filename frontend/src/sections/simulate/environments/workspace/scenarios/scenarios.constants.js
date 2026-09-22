// Scenarios-tab copy and grouping helpers.
//
// deriveUseCase / groupScenarios are copied verbatim from the designer
// ScenariosStep (REF lines 522-571). The grouping is what both the list and the
// table read: rows are bucketed by the axis the reader chose — the task the
// agent is asked to do (Goal), the caller (Persona), or the first step
// (Sub-goal) — not by the derivation kind they fell out of.

import { subTasksFor } from "src/api/simulate-environments/_fixtures/contract";

export const SCENARIOS_COPY = {
  heading: "Scenarios",
  subtitle:
    "Each scenario is one task your agent has to complete, and carries its own persona.",
  searchPlaceholder: "Search scenarios by name, task or use case…",
  addLabel: "Add scenarios",
  addComingSoon: "Coming soon",
  editLabel: "Edit scenario",
  removeLabel: "Remove from this environment",
  filterLabel: "Filter",
  filterTitle: "Filter by use case",
  clearLabel: "Clear",
  groupByLabel: "Group by",
  hideGroup: "Hide this group",
  showAll: "Show all",
  allHidden: "Every group is hidden — click Show all to bring them back.",
  noMatch: "No scenarios match your filters.",
  emptyTitle: "No scenarios yet",
  emptyBody:
    "Scenarios are normally derived from your agent when the environment is built.",
  critical: "Critical — a failure here is a release blocker",
};

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
    return { id: `rule:${short.toLowerCase()}`, label: short || "Rule enforcement" };
  }
  if (id.includes("-trap-")) {
    const table = title.split(":")[0].trim();
    if (table) return { id: `trap:${table.toLowerCase()}`, label: `${humanize(table)} data` };
    return { id: "trap:other", label: "Data traps" };
  }
  if (id.includes("-adversarial-")) return { id: `adv:${title.toLowerCase()}`, label: title || "Adversarial" };
  if (id.includes("-edge-")) return { id: `edge:${title.toLowerCase()}`, label: title || "Edge case" };
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
    const slug = first.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
    return { id: `subgoal:${slug}`, label: first };
  }
  return deriveUseCase(row);
};

export const groupScenarios = (rows, mode = "goal", env) => {
  const buckets = new Map();
  (rows || []).forEach((r) => {
    const key = groupKeyOf(r, mode, env);
    if (!buckets.has(key.id)) buckets.set(key.id, { id: key.id, label: key.label, rows: [] });
    buckets.get(key.id).rows.push(r);
  });
  // Bigger groups first — the tail of one-off adversarial/edge titles shouldn't
  // push the meaty groups out of view.
  return [...buckets.values()].sort((a, b) => b.rows.length - a.rows.length);
};
