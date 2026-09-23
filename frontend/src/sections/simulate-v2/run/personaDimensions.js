/**
 * User-defined persona dimensions.
 *
 * Per Monika's feedback ("we want custom persona dimensions … name
 * patience level explicitly"), users need to slice agent perf by
 * behavioural attributes they define — not just FutureAGI's built-
 * in dims (accent, speed, profession, background noise).
 *
 * Model: a persona can carry `dims: { patience_level: "low", price_sensitivity: "high" }`
 * — any string key/value pair. The analytics tab auto-discovers
 * every dimension name used across the run's personas and exposes
 * them in the editor's group-by / filter dropdowns.
 *
 * This module is deliberately thin — the storage is per-persona,
 * the analytics side just needs to know what dims exist.
 */

const CANDIDATE_KEYS = [
  "patience_level",
  "price_sensitivity",
  "tech_savviness",
  "tone",
  "urgency",
  "loyalty",
  "familiarity",
];

/* If the seed data doesn't populate dims, synthesise a couple from
   the persona's existing signals so the demo tab has values to
   show. Deterministic from persona slug so each persona keeps the
   same synthesised dims across renders. */
function synthesiseDims(persona) {
  if (!persona) return {};
  const slug = persona.slug || persona.name || "anon";
  const h = Array.from(String(slug)).reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 0);
  const pick = (opts, offset) => opts[(h + offset) % opts.length];
  return {
    patience_level:     pick(["low", "medium", "high"], 0),
    price_sensitivity:  pick(["low", "medium", "high"], 1),
    tech_savviness:     pick(["novice", "intermediate", "advanced"], 2),
  };
}

/** Read the effective dims for a persona — user-supplied override
 *  when present, synthesised default otherwise. */
export function personaDims(persona) {
  if (!persona) return {};
  const user = persona.dims || {};
  const synth = synthesiseDims(persona);
  return { ...synth, ...user };
}

/** Ensure every task's persona has a `dims` map before analytics
 *  reads them. Idempotent; safe to call every render. */
export function attachPersonaDims(tasks) {
  (tasks || []).forEach((t) => {
    if (!t.persona) return;
    if (!t.persona.dims) t.persona.dims = personaDims(t.persona);
  });
  return tasks;
}

/** Discover the set of dim names used across a task list — powers
 *  the editor's dynamic group-by dropdown. Returns names in a
 *  stable order (candidate order first, then any extras). */
export function readPersonaDimensions(tasks) {
  const seen = new Set();
  (tasks || []).forEach((t) => {
    const dims = personaDims(t.persona);
    Object.keys(dims || {}).forEach((k) => seen.add(k));
  });
  const ordered = CANDIDATE_KEYS.filter((k) => seen.has(k));
  seen.forEach((k) => { if (!ordered.includes(k)) ordered.push(k); });
  return ordered;
}

/** Human-friendly label for a dimension key. */
export function dimensionLabel(key) {
  return String(key || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
