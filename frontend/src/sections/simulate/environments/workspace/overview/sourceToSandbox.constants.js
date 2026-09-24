import { ORIGIN_ID } from "../../buildEnvironment/provenance.constants";
import { ruleRowsFor } from "./overview.constants";

// Copy for the source→sandbox map: the reviewability record of how each derived
// fact was read from source and what it became in the sandbox world.
export const MAP_COPY = {
  title: "How the world was built",
  subtitle:
    "Every derived fact carries where it was read from and what it became in the sandbox. A tool whose effect you set on the Contract tab shows here as confirmed.",
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
  storesRight: (n) => `${n} mapped`,
  storesEmpty: "No stores seeded for this environment.",
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

// The Contract tab's effect picker stores a singular override (`read`/`write`);
// this ledger's targets speak the plural (`reads`/`writes`), so normalize.
const NORMALIZE_EFFECT = { read: "reads", write: "writes", reads: "reads", writes: "writes" };

// The tool rows for the map: name, origin chip, and sandbox target. The
// read/write classification is a verb-heuristic guess; resolving/overriding it
// lives on the Contract tab (WorldInternalsSection's effect picker). This ledger
// is read-only, but it REFLECTS a Contract override — a resolved tool shows the
// overridden target and a "you confirmed" mark, so the reader can see which
// decisions a human made vs. which the reader made itself.
export const toolMapRows = (env, envState) => {
  const tools = env?.tools || [];
  const resolutions = envState?.toolResolutions || {};
  return tools.map((t) => {
    const stored = resolutions[t.name];
    const effect = stored ? (NORMALIZE_EFFECT[stored] || stored) : classifyTool(t);
    return {
      key: t.name,
      name: t.name,
      origin: effect === "unknown" ? ORIGIN_ID.CALL_GRAPH : ORIGIN_ID.CONFIG,
      target: effectTarget(effect),
      confirmed: !!stored,
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

// Real §6 world.stores → the same map-row shape, flattened per table. Empty
// stores yield no rows (the caller renders an empty state). The Actors group and
// its hardcoded stub were removed — nothing backed them (world.personas wasn't
// even read).
export const storeRowsFromStores = (stores = []) =>
  (Array.isArray(stores) ? stores : []).flatMap((store) =>
    (store?.tables || []).map((t) => {
      const rows = Number(t?.rows) || 0;
      return {
        key: `${store?.capability || store?.engine || "store"}:${t?.name}`,
        name: `${t?.name} · ${rows.toLocaleString()}`,
        origin: ORIGIN_ID.FIXTURE,
        target: `${t?.name} (${rows.toLocaleString()})`,
      };
    }),
  );
