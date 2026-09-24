// Scenarios-tab copy and grouping helpers.
//
// deriveUseCase / groupScenarios are copied verbatim from the designer
// ScenariosStep (REF lines 522-571). The use-case grouping is what both the
// list and the table read: rows are bucketed by the task the agent is asked to
// do, not by the derivation kind they fell out of.

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
  noMatch: "No scenarios match your filters.",
  emptyTitle: "No scenarios yet",
  emptyBody:
    "Scenarios are normally derived from your agent when the environment is built.",
  critical: "Critical — a failure here is a release blocker",
  selectAll: "Select all scenarios",
  selectRow: (name) => `Select ${name}`,
};

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

export const groupScenarios = (rows) => {
  const buckets = new Map();
  (rows || []).forEach((r) => {
    const uc = deriveUseCase(r);
    if (!buckets.has(uc.id)) buckets.set(uc.id, { id: uc.id, label: uc.label, rows: [] });
    buckets.get(uc.id).rows.push(r);
  });
  // Bigger groups first — the tail of one-off adversarial/edge titles shouldn't
  // push the meaty use-case groups out of view.
  return [...buckets.values()].sort((a, b) => b.rows.length - a.rows.length);
};
