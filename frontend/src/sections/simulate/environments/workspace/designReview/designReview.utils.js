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
