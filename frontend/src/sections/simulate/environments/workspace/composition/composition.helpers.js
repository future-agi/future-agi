import {
  BINDING_MODE_LABELS,
  displayText,
  readable,
  shortDigest,
} from "src/pages/dashboard/harness/harnessShared";

export const bindingModeLabel = (mode) =>
  BINDING_MODE_LABELS[mode] || readable(displayText(mode));

// One line for the workspace header: what the next run is pinned to.
export const compositionSummary = (composition) => {
  if (!composition) return "";
  const agent = composition.agent || {};
  const suite = composition.suite || {};
  const binding = composition.binding || {};
  return [
    [agent.name, agent.version_name].filter(Boolean).join(" "),
    `suite ${suite.name || ""} ${shortDigest(suite.digest)}`
      .replace(/\s+/g, " ")
      .trim(),
    `${bindingModeLabel(binding.mode)} binding ${shortDigest(binding.digest)}`.trim(),
  ]
    .filter(Boolean)
    .join(" · ");
};

// One row per agent tool the clone exposes: its admission and where the binding sends it.
// Tools the binding knows but admission does not list (canonical bindings carry no
// admission) still get a row, so nothing the agent can call is missing from the view.
export const alignmentRows = (composition) => {
  const alignment = composition?.alignment || {};
  const byName = new Map();
  const row = (name) => {
    if (!byName.has(name)) {
      byName.set(name, {
        name,
        admission: null,
        steps: [],
        note: null,
        kind: null,
      });
    }
    return byName.get(name);
  };
  (alignment.admission || []).forEach((item) => {
    if (!item?.name) return;
    row(item.name).admission = item;
  });
  (alignment.mapped || []).forEach((item) => {
    if (!item?.agent_tool) return;
    const entry = row(item.agent_tool);
    entry.steps = Array.isArray(item.steps) ? item.steps : [];
    entry.kind = "mapped";
  });
  (alignment.unsupported || []).forEach((item) => {
    if (!item?.agent_tool) return;
    Object.assign(row(item.agent_tool), {
      kind: "unsupported",
      note: item.reason,
    });
  });
  (alignment.extension_candidates || []).forEach((item) => {
    if (!item?.agent_tool) return;
    Object.assign(row(item.agent_tool), {
      kind: "extension",
      note: item.reason,
    });
  });
  (composition?.binding?.agent_tools || []).forEach((tool) => {
    if (!tool?.name) return;
    const entry = row(tool.name);
    if (!entry.admission && tool.admission) {
      entry.admission = { name: tool.name, action: tool.admission };
    }
  });
  return [...byName.values()];
};

export const proofSummary = (proof) => {
  const counts = { proved: 0, failed: 0, unscorable: 0 };
  Object.values(proof?.scenarios || {}).forEach((item) => {
    if (item?.status in counts) counts[item.status] += 1;
  });
  return counts;
};
