/**
 * The coverage matrix.
 *
 * "32 scenarios" says nothing about whether the hard cases are covered. This
 * cross-tabulates what is actually in the suite so an empty cell is visible —
 * the point is the gaps, not the total.
 *
 * Difficulty is derived rather than stored, because nothing asks an author to
 * grade their own scenario: a scenario that probes a rule, or runs long, is
 * harder than a routine tool call. The UI says so rather than presenting it as
 * a number somebody chose.
 */

/** Failure mode is encoded in the scenario id — that is where the pack wrote it. */
export const FAILURE_MODES = [
  { id: "core", label: "Happy path", blurb: "The routine task, done correctly." },
  { id: "rule", label: "Rule pressure", blurb: "Only succeeds if the agent breaks a rule." },
  { id: "trap", label: "Data traps", blurb: "The awkward row the seed data hides." },
  { id: "adversarial", label: "Adversarial", blurb: "Someone actively working the agent." },
  { id: "edge", label: "Edge cases", blurb: "Interruptions, retries, changes of mind." },
];

export const DIFFICULTIES = [
  { id: "easy", label: "Easy", color: "#16A34A" },
  { id: "standard", label: "Standard", color: "#CA8A04" },
  { id: "hard", label: "Hard", color: "#DC2626" },
];

export const modeOf = (row) => {
  const id = row.id || "";
  if (id.includes("-core-")) return "core";
  if (id.includes("-rule-")) return "rule";
  if (id.includes("-trap-")) return "trap";
  if (id.includes("-adversarial-")) return "adversarial";
  if (id.includes("-edge-")) return "edge";
  return "core";
};

/**
 * Derived, and deliberately simple: a critical scenario or a long one is hard.
 * Stated on screen so nobody reads it as a score somebody assigned.
 */
export const difficultyOf = (row) => {
  if (row.critical || (row.turns || 0) >= 10) return "hard";
  if ((row.turns || 0) >= 7) return "standard";
  return "easy";
};

export const personaOf = (row) => row.persona?.role || row.persona?.name || "Unknown";

export const AXES = [
  { id: "mode", label: "Failure mode", of: modeOf, keys: () => FAILURE_MODES.map((m) => m.id), labelOf: (k) => FAILURE_MODES.find((m) => m.id === k)?.label || k },
  { id: "difficulty", label: "Difficulty", of: difficultyOf, keys: () => DIFFICULTIES.map((d) => d.id), labelOf: (k) => DIFFICULTIES.find((d) => d.id === k)?.label || k },
  { id: "persona", label: "Persona", of: personaOf, keys: (rows) => [...new Set(rows.map(personaOf))].sort(), labelOf: (k) => k },
];

export const getAxis = (id) => AXES.find((a) => a.id === id) || AXES[0];

/** rows × two axes → { cols, rows, cell(r,c), total, empty } */
export const buildMatrix = (scenarios, rowAxisId, colAxisId) => {
  const rowAxis = getAxis(rowAxisId);
  const colAxis = getAxis(colAxisId);
  const rowKeys = rowAxis.keys(scenarios);
  const colKeys = colAxis.keys(scenarios);

  const counts = {};
  scenarios.forEach((s) => {
    const key = `${rowAxis.of(s)}|${colAxis.of(s)}`;
    counts[key] = (counts[key] || 0) + 1;
  });

  const cells = rowKeys.flatMap((r) => colKeys.map((c) => ({ r, c, n: counts[`${r}|${c}`] || 0 })));
  const max = Math.max(1, ...cells.map((c) => c.n));

  return {
    rowAxis, colAxis, rowKeys, colKeys, max,
    at: (r, c) => counts[`${r}|${c}`] || 0,
    total: scenarios.length,
    empty: cells.filter((c) => c.n === 0),
  };
};

/**
 * The sentence a stakeholder actually wants: not "32 scenarios" but "nothing
 * covers an adversarial user on the hard path".
 */
export const coverageGaps = (scenarios) => {
  const m = buildMatrix(scenarios, "mode", "difficulty");
  return m.empty
    .filter((c) => !(c.r === "core" && c.c === "hard")) // a hard happy path is not a real gap
    .map((c) => ({
      id: `${c.r}-${c.c}`,
      label: `${m.rowAxis.labelOf(c.r)} × ${m.colAxis.labelOf(c.c)}`,
      blurb: FAILURE_MODES.find((f) => f.id === c.r)?.blurb || "",
    }));
};

/**
 * Which rules have a scenario, and which have none.
 *
 * A suite can look complete on every axis this file already measures — mode,
 * difficulty, persona — while a hard rule the environment exists to enforce is
 * tested by nothing at all. That is the coverage gap that matters: the others
 * cost you variety, this one costs you the guardrail.
 *
 * Matched on the rule's own words rather than a stored link, because scenarios
 * are generated and edited independently of the rule list — a link would go
 * stale silently, and a stale link is worse than no link.
 */
export const ruleCoverage = (env, scenarios = []) => {
  const rules = env?.rules || [];
  const words = (t = "") => t.toLowerCase().match(/[a-z_]{4,}/g) || [];
  const STOP = new Set(["never", "always", "must", "with", "that", "this", "from", "before", "after", "their", "your", "them", "than", "into", "when", "only", "another", "detail", "details"]);

  return rules.map((rule) => {
    const key = words(rule).filter((w) => !STOP.has(w));
    const covering = scenarios.filter((sc) => {
      const hay = `${sc.title} ${sc.task} ${sc.expected}`.toLowerCase();
      /* Two content words in common is the threshold — one matches by accident
         ("refund" appears everywhere), three almost never matches at all. */
      return key.filter((w) => hay.includes(w)).length >= 2;
    });
    return { rule, scenarios: covering, count: covering.length, critical: covering.some((s) => s.critical) };
  });
};

export const uncoveredRules = (env, scenarios) =>
  ruleCoverage(env, scenarios).filter((r) => r.count === 0);
