import { getSurface } from "src/api/simulate-environments/_fixtures/surfaces";
import { ORIGIN_ID } from "../../buildEnvironment/provenance.constants";
import { ruleRowsFor } from "./overview.constants";

// Copy for the source→sandbox map: the reviewability record of how each derived
// fact was read from source and what it became in the sandbox world.
export const MAP_COPY = {
  title: "How the world was built",
  subtitle:
    "Every derived fact carries where it was read from and what it became in the sandbox. Rows the reader could not classify carry a resolve control right on the row.",
  readHead: "Read from source",
  sandboxHead: "In the sandbox world",
  needsAnswer: "Needs your answer",
  confirmed: "you confirmed",
  groups: {
    tools: { label: "Tools", hint: "from the agent config & call-graph" },
    rules: { label: "Rules", hint: "from policy.yaml, enforced" },
    stores: { label: "Stores", hint: "from your fixtures" },
    actors: { label: "Actors", hint: "from the call-graph & prompt" },
  },
  toolsRight: (n, toAnswer) => `${n} mapped${toAnswer ? ` · ${toAnswer} to answer` : ""}`,
  rulesRight: (n) => `${n} mapped`,
  storesRight: (n) => `${n} mapped · 1 derived`,
  actorsRight: (mapped, derived) => `${mapped} mapped · ${derived} derived`,
  derivedStore: "appointments (empty)",
  resolve: {
    question: (name) => `Does ${name} change data?`,
    why: "Called from the agent's code, unnamed in the prompt — we can't tell from static analysis alone.",
    confirm: (label) => `Confirm ${label.toLowerCase()}`,
    secondary: "Ask a teammate",
    options: [
      { id: "reads", label: "Read-only", detail: "scenarios call it directly", suggested: true },
      { id: "writes", label: "Writes data", detail: "writes hit the sandbox, never production" },
    ],
  },
};

// Whether a tool reads, writes or can't be classified from its name alone — the
// reader's static-analysis guess before a human confirms.
export const classifyTool = (tool) => {
  const n = (tool?.name || "").toLowerCase();
  if (/^(book|create|reschedule|update|cancel|delete|send|post|upsert|issue|generate)/.test(n)) return "writes";
  if (/^(get|read|check|list|search|fetch|verify|lookup)/.test(n)) return "reads";
  return "unknown";
};

// What a classified effect becomes on the sandbox side.
export const effectTarget = (effect) => {
  if (effect === "writes") return "writes to the sandbox";
  if (effect === "reads") return "reads from fixture state";
  return "stubbed · returns fixture";
};

// Single out the last tool the reader can't classify. If every tool is
// classifiable, the last one still carries the resolve affordance so the review
// path is always exercised.
export const unresolvedToolIndex = (tools = []) => {
  for (let i = tools.length - 1; i >= 0; i -= 1) {
    if (classifyTool(tools[i]) === "unknown") return i;
  }
  return tools.length ? tools.length - 1 : -1;
};

// The tool rows for the map: name, origin chip, sandbox target, and whether the
// row is still held open for the reader to resolve.
export const toolMapRows = (env, envState) => {
  const tools = env?.tools || [];
  const resolutions = envState?.toolResolutions || {};
  const unresolved = unresolvedToolIndex(tools);
  return tools.map((t, i) => {
    const effect = classifyTool(t);
    const stored = resolutions[t.name];
    const isUnresolved = i === unresolved && !stored;
    return {
      key: t.name,
      name: t.name,
      origin: effect === "unknown" || isUnresolved ? ORIGIN_ID.CALL_GRAPH : ORIGIN_ID.CONFIG,
      target: stored ? effectTarget(stored) : isUnresolved ? null : effectTarget(effect),
      confirmed: !!stored,
      isUnresolved,
    };
  });
};

// What a rule becomes at the sandbox boundary, keyed off its subject.
export function ruleTarget(subject, origin) {
  if (origin === ORIGIN_ID.DOC) return "held for review · click to accept";
  const s = (subject || "").toLowerCase();
  if (s.includes("double") || s.includes("overlap")) return "blocks overlapping slots";
  if (s.includes("hour") || s.includes("time")) return "blocks off-hours attempts";
  if (s.includes("cancel") || s.includes("window") || s.includes("return")) return "blocks late cancels";
  if (s.includes("refund") || s.includes("supervisor") || s.includes("approval")) return "requires supervisor approval";
  if (s.includes("escalat")) return "routes to human handoff";
  return "enforced at the sandbox boundary";
}

// The rule rows for the map: same subjects as HardRulesCard, with code-enforced
// rules shown as read from policy.yaml and each mapped to what it blocks.
export const ruleMapRows = (env) =>
  ruleRowsFor(env).map((r) => ({
    key: r.id,
    name: r.subject,
    origin: r.origin === ORIGIN_ID.CODE ? ORIGIN_ID.POLICY : r.origin,
    target: ruleTarget(r.subject, r.origin),
  }));

// The seeded stores that fill the world before the agent arrives.
export const storeMapRows = (env) => {
  const tables = env?.seed?.tables || [];
  return tables.map((t) => ({
    key: t.name,
    name: `${t.name} · ${t.rows.toLocaleString()}`,
    origin: ORIGIN_ID.FIXTURE,
    target: `${t.name} (${t.rows.toLocaleString()})`,
  }));
};

// The actors the world stands up: some mapped from source, some derived.
export const actorMapRows = (env) => {
  const tools = env?.tools || [];
  const surface = getSurface(env?.surface);
  return [
    { key: "transfer", name: "transfer target", origin: ORIGIN_ID.PROMPT, target: "Supervisor persona", mapped: true },
    { key: "external", name: tools[0]?.name || "external_service", origin: ORIGIN_ID.CALL_GRAPH, target: `${env?.domain || "Backend"} service`, mapped: true },
    { key: "caller", name: null, origin: null, target: `Caller — ${surface?.blurb?.split(" ")[0] || "the user"}`, mapped: false },
    { key: "clock", name: null, origin: null, target: "Clock", mapped: false },
  ];
};
