/*
  Reviewing trace drafts (roadmap traces.md "Review and repair the proposal"):
  what each draft is built on, kept honest. Missing starting state is added,
  replayed from recordings, or the draft stays blocked — never called exact.
  Production records and people map to the world; integrations that only
  exist in production are named. What happened and what should happen are
  separate, and an inferred fact is never shown as observed.
*/
import { productionTracesOf } from "./traceImport";
import { rulesOf } from "./envModel";
import { currentUser } from "./scenarioProvenance";

/** The source traces a draft came from, with their evidence. */
export const draftSourcesOf = (row, env, envState) => {
  const ids = row?.traceImport?.sourceIds || [];
  const all = productionTracesOf(env, envState);
  return ids.map((id) => all.find((t) => t.traceId === id)).filter(Boolean);
};

/* How a draft's starting state is settled, and the evidence class that follows. */
export const STATE_MODES = {
  observed: { label: "Recorded in the trace", evidence: "Exact — the starting state was recorded" },
  fixture: { label: "Add the starting data", evidence: "Reconstructed — starting data you supplied, cited below" },
  replay: { label: "Replay the recorded responses", evidence: "Recorded replay — tests the reaction to what came back; can't show what state was before" },
  synthetic: { label: "Labelled synthetic reproduction", evidence: "Synthetic — a made-up starting state, labelled as such on every result" },
  blocked: { label: "Keep blocked", evidence: "Blocked — not runnable until the starting state is settled" },
};

/** The draft's review so far, with defaults from its evidence. */
export const reviewOf = (row, sources) => {
  const r = row?.traceReview || {};
  const hasState = sources.length > 0 && sources.every((s) => s.evidence.startingState);
  return {
    stateMode: r.stateMode || (hasState ? "observed" : "blocked"),
    fixture: r.fixture || null,
    redaction: r.redaction || {},
    happened: r.happened ?? null,
    should: r.should ?? null,
    assumptions: r.assumptions || {},
    mapping: r.mapping || {},
    validated: r.validated || null,
  };
};

/** Seeded records a fixture can point at — the world's tables. */
export const fixtureOptionsOf = (env) => {
  const tables = Array.isArray(env?.seed) ? env.seed : env?.seed?.tables || [];
  return tables.slice(0, 4).flatMap((t, i) => [
    { id: `${t.name}:a`, label: `${t.name} · seeded row ${1001 + i * 7}${t.note ? ` (${t.note.split(",")[0]})` : ""}` },
  ]);
};

/** Redaction: each kind found, what replaces it, and the rule. */
export const REDACTION_RULES = { fake: "Fake value", mask: "Mask (•••)", keep: "Keep" };
export const redactionsOf = (row) => {
  const k = row?.personalData?.kinds || {};
  return [
    k.names && { id: "names", label: "Names", n: k.names, example: "Riley Shaw", canKeep: false },
    k.phones && { id: "phones", label: "Phone numbers", n: k.phones, example: "+1 415 555 0142", canKeep: false },
    k.emails && { id: "emails", label: "Emails", n: k.emails, example: "riley.shaw@example.com", canKeep: false },
    k.addresses && { id: "addresses", label: "Addresses", n: k.addresses, example: "1452 Oak Street", canKeep: false },
    k.numbers && { id: "numbers", label: "Account or card numbers", n: k.numbers, example: "ACC-•••• 4821", canKeep: true },
  ].filter(Boolean);
};

/** Records, people and tools the trace touched, mapped to the world. */
export const mappingsOf = (row, env, sources) => {
  const tools = (env?.tools || []).map((t) => t.name);
  const topic = String(row?.useCase || row?.title || "").toLowerCase();
  const matched = tools.filter((t) => topic.includes(t.split("_").slice(1).join(" ").replace(/s$/, "")) || /lookup|find|get/.test(t)).slice(0, 2);
  const tables = (Array.isArray(env?.seed) ? env.seed : env?.seed?.tables || []).map((t) => t.name);
  const prodOnly = sources.some((s) => s.evidence.tools) ? ["zendesk_create_ticket"] : [];
  return [
    { id: "account", kind: "Record", from: "The caller's account (production ID)", to: tables[0] ? `${tables[0]} · a seeded row` : "no seeded table holds it — the caller describes it instead" },
    { id: "caller", kind: "Person", from: "The caller", to: row?.persona?.name ? `Persona · ${row.persona.name} (${row.persona.archetype || "inferred"})` : "Persona · inferred" },
    ...matched.map((t) => ({ id: `tool:${t}`, kind: "Tool", from: `${t} (as called in production)`, to: `${t} · the world answers it` })),
    ...prodOnly.map((t) => ({ id: `tool:${t}`, kind: "Tool", from: `${t} (production only)`, to: null, unresolved: true })),
  ];
};

/** The two separate fields, prefilled: what the trace shows, and what the policy asks for. */
export const happenedOf = (row, sources) => {
  const failed = sources.some((s) => s.outcome === "failed");
  return failed
    ? `The agent said "Let me look into that for you" and the call ended without the caller's ${String(row?.useCase || "request").replace(/^Caller chasing their /i, "")} being resolved — it claimed progress it hadn't made.`
    : "The agent resolved what the caller asked for and confirmed it before ending the call.";
};
export const shouldOf = (row, env, envState) => {
  const rule = rulesOf(env, envState)[0];
  return `Verify the caller, check the actual record with the tools, and tell the caller only what the tools confirm${rule ? ` — per ${rule.id}: "${rule.text}"` : ""}. If a tool fails, say so and offer the next step; never claim it's done.`;
};

/** Facts the draft rests on that were inferred, not observed. */
export const assumptionsOf = (row) => [
  { id: "persona", line: `The caller is a ${String(row?.persona?.archetype || "frustrated repeat caller").toLowerCase()} — inferred from their wording` },
  { id: "intent", line: `They want the ${String(row?.useCase || "request").replace(/^Caller chasing their /i, "")} resolved today — inferred, not stated as a deadline` },
];

/** A draft's status in the review: blocked, needs input, or ready to add. */
export const draftStatusOf = (row, review, maps) => {
  if (row?.traceReview?.decision === "rejected") return { id: "rejected", label: "Rejected" };
  if (review.stateMode === "blocked") return { id: "blocked", label: "Blocked", why: "Starting state not settled" };
  const open = Object.values(review.assumptions).filter((a) => a === "blocked").length;
  if (open) return { id: "blocked", label: "Blocked", why: "An inferred fact is kept blocked" };
  if (maps.some((m) => m.unresolved && !review.mapping[m.id])) return { id: "needs", label: "Needs input", why: "A production-only integration isn't resolved" };
  const pending = assumptionsOf(row).filter((a) => !review.assumptions[a.id]).length;
  if (pending) return { id: "needs", label: "Needs input", why: `${pending} inferred fact${pending === 1 ? "" : "s"} to accept or replace` };
  if (!review.validated) return { id: "needs", label: "Needs input", why: "Not validated yet" };
  return { id: "ready", label: "Ready to add" };
};

/** Drafts that look like the same situation — merge keeps every source. */
export const duplicatesOf = (rows) => {
  const by = {};
  rows.forEach((r) => {
    const k = String(r.useCase || r.title).toLowerCase();
    (by[k] = by[k] || []).push(r.id);
  });
  return Object.values(by).filter((ids) => ids.length > 1);
};

/** The patch that saves part of one draft's review. */
export const reviewPatch = (envState, id, fields) => ({
  scenarios: (envState?.scenarios || []).map((r) =>
    r.id === id ? { ...r, traceReview: { ...(r.traceReview || {}), ...fields } } : r,
  ),
});

/** Merge duplicates into the first: every source kept, the others dropped from the draft list. */
export const mergePatch = (envState, ids) => {
  const [keep, ...rest] = ids;
  const rows = envState?.scenarios || [];
  const sources = [...new Set(ids.flatMap((id) => rows.find((r) => r.id === id)?.traceImport?.sourceIds || []))];
  return {
    scenarios: rows
      .filter((r) => !rest.includes(r.id))
      .map((r) => (r.id === keep ? { ...r, traceImport: { ...r.traceImport, sourceIds: sources, merged: rest } } : r)),
  };
};

/** Rejecting keeps the draft in the import's history with the reason. */
export const rejectPatch = (envState, row, reason) => ({
  scenarios: (envState?.scenarios || []).filter((r) => r.id !== row.id),
  traceImports: [
    ...(envState?.traceImports || []),
    { importId: row.traceImport?.id, draftId: row.id, title: row.title, sources: row.traceImport?.sourceIds, rejected: { reason, by: currentUser().name, at: new Date().toISOString() } },
  ],
});
