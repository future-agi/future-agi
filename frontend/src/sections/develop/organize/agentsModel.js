// Attach datasets to agents (W9-SCOPE §5 A7): what the Agents dialog shows
// and sends. Agents are Agent Playground graphs, by id. Pure.
import { agentsProblem } from "./orgRules";

/** For each graph: how many of the datasets have it attached. */
export function attachState(datasets, graphId) {
  const n = (datasets ?? []).filter((d) =>
    (d.agent_ids ?? []).includes(graphId),
  ).length;
  if (!n) return "none";
  return n === datasets.length ? "all" : "some";
}

/**
 * Next agent ids per dataset after the user's choices.
 * @param {{id, agent_ids?: string[]}[]} datasets
 * @param {{[graphId: string]: "attach"|"detach"}} choices
 * @returns {{[datasetId: string]: string[]}}
 */
export function nextAgentIds(datasets, choices) {
  const out = {};
  (datasets ?? []).forEach((d) => {
    const set = new Set(d.agent_ids ?? []);
    Object.entries(choices ?? {}).forEach(([g, c]) => {
      if (c === "attach") set.add(g);
      if (c === "detach") set.delete(g);
    });
    out[d.id] = [...set];
  });
  return out;
}

/** The datasets whose agent ids change, with their next ids. */
export function agentChanges(datasets, choices) {
  const next = nextAgentIds(datasets, choices);
  return (datasets ?? [])
    .filter((d) => {
      const a = [...(d.agent_ids ?? [])].sort().join(",");
      const b = [...next[d.id]].sort().join(",");
      return a !== b;
    })
    .map((d) => ({ ...d, next: next[d.id] }));
}

/** The first problem among the changes (the 20-agent limit), or null. */
export function agentChangesProblem(changes, graphIds) {
  for (const c of changes ?? []) {
    const p = agentsProblem(c.next, { graphIds });
    if (p) return p;
  }
  return null;
}

/**
 * Graphs the datasets' own runs targeted that are not attached to all of
 * them (from each item's `suggested_agents`), once each.
 */
export function suggestedGraphs(datasets) {
  const seen = new Map();
  (datasets ?? []).forEach((d) =>
    (d.suggested_agents ?? []).forEach((g) => {
      if (!seen.has(g.id)) seen.set(g.id, g);
    }),
  );
  return [...seen.values()];
}

/** The dialog's opening line. */
export const agentsCopy = (n) =>
  n === 1
    ? "Attach this dataset to the agents it tests. Attached datasets show in the Agent filter and group here, with each agent's latest run on the dataset."
    : `Attach these ${n} datasets to the agents they test. Attached datasets show in the Agent filter and group here, with each agent's latest run on the dataset.`;

/** `Support Agent · #4B 86.6%` / `Support Agent · no run yet`. */
export function agentChipText(agent) {
  const r = agent?.last_run;
  if (!r) return `${agent?.name ?? agent?.id} · no run yet`;
  return `${agent.name} · ${r.label}${r.display ? ` ${r.display}` : ""}`;
}

/**
 * `Latest run on Support Agent: #4B (v3), 86.6% Success rate. Baseline #2B
 * 79.4%.` The metric and baseline come from the dataset row.
 */
export function agentTooltip(agent, row) {
  const r = agent?.last_run;
  if (!r) return `No finished run on ${agent?.name ?? "this agent"} yet.`;
  const metric =
    row?.baseline?.metric_name ?? row?.best_run?.metric_name ?? null;
  const head = `Latest run on ${agent.name}: ${r.label}${r.version ? ` (${r.version})` : ""}${
    r.display ? `, ${r.display}${metric ? ` ${metric}` : ""}` : ""
  }.`;
  const b = row?.baseline;
  const base =
    b?.label && b?.display ? ` Baseline ${b.label} ${b.display}.` : "";
  return `${head}${base}`;
}

/** The existing builder link of a graph (RunRowMenu's). */
export const agentBuilderHref = (graphId) =>
  `/dashboard/agents/playground/${graphId}/build`;
