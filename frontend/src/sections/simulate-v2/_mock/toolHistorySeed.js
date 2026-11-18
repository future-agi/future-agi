/*
  Seed 39 (T21, keep test history): two agents that lost a tool in a new
  version, so the scenarios that need it read as Blocked with the reason —

  · the returns line (env-returns-line): its newest version dropped the tool,
    so those scenarios are Blocked now;
  · the travel line (env-chat-travel): one version dropped it and the next
    put it back, so the same scenarios read Back, history kept.

  The tool is the one the fewest scenarios (at least two) need, so most of the
  set is untouched. Versions and nudges come from the real detectors — nothing
  here says what is stale; decoupled.js works it out.
*/
import { detectNudges, requiredCapsOf } from "./decoupled";
import { agentToolsFor } from "./toolFit";
import { agentVersions } from "./versions";

export const TOOL_HISTORY = [
  { envId: "env-returns-line", back: false, daysOld: 1 },
  { envId: "env-chat-travel", back: true, daysOld: 3 },
];

const iso = (daysOld) => new Date(Date.now() - daysOld * 86400000).toISOString();

const pickTool = (env, es, tools) => {
  const rows = (es.scenarios || []).filter((r) => !r.archived && !r.retired && !r.proposed);
  const counts = tools
    .map((t) => [t.name, rows.filter((r) => requiredCapsOf(r, env, es).includes(t.name)).length])
    .filter(([, n]) => n >= 2 && n < rows.length)
    .sort((a, b) => a[1] - b[1]);
  return counts[0]?.[0] || null;
};

const withVersion = (es, label, tools, note, daysOld) => ({
  ...es,
  agentVersions: [
    ...(es.agentVersions || []),
    { id: `agent-${label}`, label, note, reach: "endpoint", createdAt: iso(daysOld), tools },
  ],
});

const addHistory = (env, es, { back, daysOld }) => {
  if (!env || !es || (es.nudges || []).some((n) => n.kind === "remove")) return es;
  const list = agentVersions(es);
  const prev = list[list.length - 1]?.label;
  if (!prev) return es;
  const tools = agentToolsFor(env, es, prev);
  const tool = pickTool(env, es, tools);
  if (!tool) return es;
  /* Numbered after the newest version, which may skip numbers (v1, v3, v5…). */
  const n = Math.max(...list.map((v) => Number(String(v.label).replace(/\D/g, "")) || 0)) + 1;
  const dropped = `v${n}`;
  let next = withVersion(
    es,
    dropped,
    tools.filter((t) => t.name !== tool),
    `${tool} taken out — the agent hands that step to a person.`,
    daysOld + (back ? 1 : 0),
  );
  const remove = detectNudges(env, next, dropped, prev).nudges.find((x) => x.kind === "remove" && x.capability === tool);
  if (!remove) return es;
  next = {
    ...next,
    nudges: [
      ...(next.nudges || []),
      { ...remove, at: iso(daysOld + (back ? 1 : 0)), ...(back && { status: "approved", answeredAt: iso(daysOld) }) },
    ],
  };
  if (back) next = withVersion(next, `v${n + 1}`, tools, `${tool} back in — the hand-off is gone again.`, daysOld);
  /* An agent that names its active version follows the newest one. */
  const values = next.agent?.values;
  if (values?.activeVersion?.label) {
    const label = back ? `v${n + 1}` : dropped;
    next = { ...next, agent: { ...next.agent, values: { ...values, activeVersion: { ...values.activeVersion, label } } } };
  }
  return next;
};

/** byEnv with both cases added; environments it can't find are left alone. */
export const withToolHistory = (byEnv, environments) => {
  const out = { ...byEnv };
  TOOL_HISTORY.forEach((c) => {
    const env = (environments || []).find((e) => e.id === c.envId);
    if (!out[c.envId] || !env) return;
    try {
      out[c.envId] = addHistory(env, out[c.envId], c);
    } catch {
      /* a seed that can't be read is left as it was */
    }
  });
  return out;
};
