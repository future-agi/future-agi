import { SOURCE_LABEL } from "./designReview.constants";

export const sourceLabel = (source) => SOURCE_LABEL[source] || null;

export function commonSource(sources) {
  const counts = new Map();
  sources
    .filter(Boolean)
    .forEach((one) => counts.set(one, (counts.get(one) || 0) + 1));
  return [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || null;
}

export function tableUsage(tables, tools) {
  const names = new Set(tables.map((table) => table.name));
  const byTable = new Map(
    tables.map((table) => [table.name, { changes: [], reads: [] }]),
  );
  const none = [];
  tools.forEach((tool) => {
    const changed = (tool.tables_changed || []).filter((name) =>
      names.has(name),
    );
    const read = (tool.tables_read || []).filter(
      (name) => names.has(name) && !changed.includes(name),
    );
    changed.forEach((name) => byTable.get(name).changes.push(tool));
    read.forEach((name) => byTable.get(name).reads.push(tool));
    if (!changed.length && !read.length) none.push(tool);
  });
  return { byTable, none };
}

export const plannedScenarios = (useCases) =>
  useCases.reduce((total, one) => total + (one.planned_scenarios || 0), 0);

export function isParked(createdAt, now, continuedAt, parkAfter) {
  const since = Math.max(
    createdAt ? Date.parse(createdAt) || 0 : 0,
    continuedAt || 0,
  );
  return since > 0 && now - since >= parkAfter;
}

const STEP_STAGES = {
  world: [
    "queued",
    "acquiring_source",
    "understanding_agent",
    "generating_environment",
    "building_environment",
    "generating_data",
  ],
  scenarios: [
    "generating_scenarios",
    "validating_environment",
    "validating_scenarios",
  ],
  release: [
    "connecting_agent",
    "running",
    "grading",
    "uploading_artifacts",
    "finalizing",
    "cleaning_up",
  ],
};

export const BUILD_STEP_TAB = {
  world: "world",
  scenarios: "scenarios",
  release: "overview",
};

export function approvedBuildSteps(stage) {
  const order = ["start", "world", "scenarios", "release"];
  const at =
    stage === "completed"
      ? order.length
      : Math.max(
          1,
          order.findIndex((kind) => (STEP_STAGES[kind] || []).includes(stage)),
        );
  return order.map((kind, index) => ({
    kind,
    state: index < at ? "done" : index === at ? "running" : "pending",
  }));
}

export const kept = (items) => (items || []).filter((item) => !item.removed);
