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


/* ────────────────────────────────────────────────────────────────────────────
 * PRD-aligned six-axis coverage (Story 9).
 *
 * The block above is the older, product-facing "mode × difficulty" cut we
 * keep for the compare view and the legacy CoverageMatrix caller. Below is
 * the six-axis framework the PRD mandates:
 *
 *   AC-9.1  Every scenario = coordinate over T · W · D · X · I · O
 *   AC-9.9  Force rare-catastrophic O cells even if pairwise skips them
 *   AC-9.10 Coverage report — cells covered / masked / sampled, per axis
 *   AC-9.11 Coverage dial (smoke → deep audit)
 *   TC-9.13 Report against six axes AND the roadmap's 7 factors
 *
 * A scenario's coordinate is derived from its already-existing fields —
 * name/title/task/situation/persona/turns/mode — rather than requiring a
 * migration to a new shape. The derivations are deliberately simple and
 * commented so anyone can see what the platform is inferring.
 * ────────────────────────────────────────────────────────────────────────── */

/* Axis T — Task intent. The 12 closed operations (§9.2, "Every task = an
   Operation applied to a domain Object"). We derive the operation from
   scenario name/title/task keywords; the object side is not yet
   surfaced (comes from the agent's own tool environment). */
export const T_OPS = [
  { id: "retrieve",   label: "Retrieve",   group: "Info" },
  { id: "compare",    label: "Compare",    group: "Info" },
  { id: "explain",    label: "Explain",    group: "Info" },
  { id: "diagnose",   label: "Diagnose",   group: "Info" },
  { id: "create",     label: "Create",     group: "Write" },
  { id: "update",     label: "Update",     group: "Write" },
  { id: "cancel",     label: "Cancel",     group: "Write" },
  { id: "execute",    label: "Execute",    group: "Write" },
  { id: "configure",  label: "Configure",  group: "Write" },
  { id: "authenticate", label: "Auth",     group: "Meta" },
  { id: "navigate",   label: "Navigate",   group: "Meta" },
  { id: "handoff",    label: "Handoff",    group: "Meta" },
];

const T_KEYWORDS = {
  cancel:       ["cancel", "delete", "reverse", "revoke", "remove", "refund", "refuse", "issue-a-refund"],
  authenticate: ["verify", "authenticate", "auth", "otp", "identity", "consent", "confirm-identity"],
  handoff:      ["escalate", "handoff", "handover", "route to", "transfer", "supervisor"],
  execute:      ["execute", "process", "charge", "pay", "issue-refund", "send-replacement", "apply", "credit"],
  create:       ["create", "issue", "ship", "open", "initiate", "start-a", "send-replacement", "book"],
  update:       ["update", "change", "modify", "amend", "edit"],
  configure:    ["configure", "set-rule", "set-up", "enable", "disable"],
  compare:      ["compare", "decide", "choose", "vs", "pick"],
  explain:      ["explain", "walk through", "clarify", "describe", "policy"],
  diagnose:     ["diagnose", "troubleshoot", "why", "investigate", "double-charge"],
  navigate:     ["navigate", "guide", "next-step", "route"],
  retrieve:     ["look-up", "lookup", "get", "check", "status", "read", "fetch", "find"],
};

export const opOf = (row) => {
  const hay = `${row.id || ""} ${row.title || row.name || ""} ${row.task || ""}`.toLowerCase();
  // Check most-specific first (auth/handoff/cancel are stronger signals than a
  // generic retrieve match). Order below reflects that.
  const order = ["cancel", "authenticate", "handoff", "execute", "create", "update",
                 "configure", "compare", "explain", "diagnose", "navigate", "retrieve"];
  for (const op of order) {
    if (T_KEYWORDS[op].some((k) => hay.includes(k))) return op;
  }
  return "retrieve"; // conservative default; "look up" is the null intent
};

/* Axis W — Counterparty (§9.2: `(life-stage, literacy, language, fidelity,
   role, auth)`). We surface life-stage as the primary read because it's
   what shows on the row's persona label. */
export const W_LIFESTAGE = [
  { id: "child",   label: "Child" },
  { id: "youth",   label: "Youth (18–29)" },
  { id: "adult",   label: "Adult (30–59)" },
  { id: "senior",  label: "Senior (60+)" },
  { id: "business", label: "Business/Pro" },
];
export const lifeStageOf = (row) => {
  const age = Number(row?.persona?.age || row?.persona?.ageBand || "").toString();
  const m = age.match(/(\d+)/);
  const n = m ? Number(m[1]) : NaN;
  const role = (row?.persona?.role || "").toLowerCase();
  if (role.includes("business") || role.includes("owner") || role.includes("pro")) return "business";
  if (Number.isFinite(n)) {
    if (n < 18) return "child";
    if (n < 30) return "youth";
    if (n < 60) return "adult";
    return "senior";
  }
  if (role.includes("senior") || role.includes("elderly")) return "senior";
  if (role.includes("young") || role.includes("teen")) return "youth";
  return "adult";
};

/* Axis D — Disposition (§9.2: `(valence, arousal, coherence,
   cooperativeness, trajectory)`). Primary read: valence. */
export const D_VALENCE = [
  { id: "calm",       label: "Calm" },
  { id: "urgent",     label: "Urgent" },
  { id: "frustrated", label: "Frustrated" },
  { id: "angry",      label: "Angry / hostile" },
  { id: "confused",   label: "Confused" },
];
export const valenceOf = (row) => {
  const hay = `${row.situation || ""} ${row.name || row.title || ""}`.toLowerCase();
  if (/(angry|hostile|aggressive|abusive|shout|yell)/.test(hay)) return "angry";
  if (/(frustrat|upset|annoyed|push(es)?\s*back|distressed)/.test(hay)) return "frustrated";
  if (/(urgent|in a hurry|rush|emergency|now|asap)/.test(hay)) return "urgent";
  if (/(confused|doesn'?t trust|first[- ]?time|skeptical|not sure|unsure)/.test(hay)) return "confused";
  return "calm";
};

/* Axis X — Interface & environment (§9.2 five params, x1-x5). Primary read:
   medium fidelity (clean / noisy / degraded). Instantiated per modality —
   here from the env surface. */
export const X_FIDELITY = [
  { id: "clean",      label: "Clean"       },
  { id: "noisy",      label: "Noisy"       },
  { id: "degraded",   label: "Degraded"    },
  { id: "interrupted", label: "Interrupted" },
];
export const fidelityOf = (row, env) => {
  const surface = (env?.surface || row?.surface || "").toLowerCase();
  const hay = `${row.situation || ""} ${row.name || row.title || ""}`.toLowerCase();
  if (/(cross[- ]?talk|background|noise|codec|packet|jitter|glitch)/.test(hay)) return "noisy";
  if (/(interrupt|barge[- ]?in|cut off|cutoff)/.test(hay)) return "interrupted";
  if (/(offline|latency|slow|drop|timeout|degraded)/.test(hay)) return "degraded";
  // Voice defaults noisier than chat.
  if (surface.includes("voice")) return "noisy";
  return "clean";
};

/* Axis I — Interaction dynamics (§9.2: turn structure, tempo, continuity,
   memory load). Primary read: turn structure derived from turns. */
export const I_STRUCTURE = [
  { id: "single",  label: "Single-turn" },
  { id: "short",   label: "Short (2–4)" },
  { id: "medium",  label: "Medium (5–9)" },
  { id: "long",    label: "Long (10+)" },
];
export const structureOf = (row) => {
  const t = Number(row.turns || 0);
  if (t >= 10) return "long";
  if (t >= 5) return "medium";
  if (t >= 2) return "short";
  return "single";
};

/* Axis O — Adversarial/safety overlay (§9.2 `o1` type, o2 vector, o3
   intensity). Primary read: overlay type. The forced-cells list below
   materialises AC-9.9. */
export const O_TYPES = [
  { id: "none",             label: "None" },
  { id: "prompt-injection", label: "Prompt-injection", critical: true },
  { id: "social-eng",       label: "Social engineering", critical: true },
  { id: "pii",              label: "PII / privacy",      critical: true },
  { id: "out-of-scope",     label: "Out-of-scope" },
  { id: "destructive",      label: "Destructive / irreversible", critical: true },
  { id: "vulnerable",       label: "Minor / vulnerable", critical: true },
  { id: "emergency",        label: "Emergency / crisis", critical: true },
  { id: "fraud",            label: "Fraud / abuse" },
  { id: "jailbreak",        label: "Jailbreak",          critical: true },
  { id: "policy-evasion",   label: "Policy evasion" },
  { id: "data-exfil",       label: "Data exfiltration",  critical: true },
];
export const overlayOf = (row) => {
  const hay = `${row.id || ""} ${row.title || row.name || ""} ${row.task || ""} ${row.situation || ""}`.toLowerCase();
  if (/(prompt[- ]?inject|system prompt|ignore previous)/.test(hay)) return "prompt-injection";
  if (/(social[- ]?engineer|impersonat|pretend|claim(s|ed)?\s*to\s*be)/.test(hay)) return "social-eng";
  if (/(pii|privacy|ssn|passport|card number|data leak|cvv)/.test(hay)) return "pii";
  if (/(destructive|irreversible|permanent(ly)?|delete forever)/.test(hay)) return "destructive";
  if (/(minor|underage|vulnerable|elderly at risk|impaired)/.test(hay)) return "vulnerable";
  if (/(emergency|crisis|911|999|suicide|self[- ]?harm|urgent medical)/.test(hay)) return "emergency";
  if (/(jailbreak|dan[- ]mode|unlock)/.test(hay)) return "jailbreak";
  if (/(fraud|scam|abuse)/.test(hay)) return "fraud";
  if (/(exfil|leak data|extract data)/.test(hay)) return "data-exfil";
  if (/(policy[- ]?evasion|bypass policy|work[- ]?around)/.test(hay)) return "policy-evasion";
  if (/(out[- ]?of[- ]?scope|off[- ]topic|not my job)/.test(hay)) return "out-of-scope";
  // adversarial mode from the failure-mode axis is a strong signal even if
  // the keywords didn't hit — the pack authored it that way.
  if (modeOf(row) === "adversarial") return "social-eng";
  return "none";
};

/** The full PRD axis registry. Each axis exposes `universe` (all possible
 *  levels) and `of(row, env)` (which level this scenario occupies). */
export const PRD_AXES = [
  { id: "T", label: "Task intent",     universe: T_OPS,       of: (r) => opOf(r),               labelOf: (k) => T_OPS.find((o) => o.id === k)?.label || k },
  { id: "W", label: "Counterparty",    universe: W_LIFESTAGE, of: (r) => lifeStageOf(r),        labelOf: (k) => W_LIFESTAGE.find((o) => o.id === k)?.label || k },
  { id: "D", label: "Disposition",     universe: D_VALENCE,   of: (r) => valenceOf(r),          labelOf: (k) => D_VALENCE.find((o) => o.id === k)?.label || k },
  { id: "X", label: "Interface",       universe: X_FIDELITY,  of: (r, e) => fidelityOf(r, e),   labelOf: (k) => X_FIDELITY.find((o) => o.id === k)?.label || k },
  { id: "I", label: "Interaction",     universe: I_STRUCTURE, of: (r) => structureOf(r),        labelOf: (k) => I_STRUCTURE.find((o) => o.id === k)?.label || k },
  { id: "O", label: "Adversarial",     universe: O_TYPES,     of: (r) => overlayOf(r),          labelOf: (k) => O_TYPES.find((o) => o.id === k)?.label || k },
];
export const getPrdAxis = (id) => PRD_AXES.find((a) => a.id === id) || PRD_AXES[0];

/** Rare-catastrophic O overlays the PRD (AC-9.9) says must be present even
 *  if pairwise sampling skips them. */
export const FORCED_O_CELLS = [
  "destructive", "vulnerable", "emergency", "pii", "prompt-injection",
];

/** Per-axis coverage: how many of the axis's possible levels are actually
 *  hit by the current suite, and which are missing. */
export const perAxisCoverage = (scenarios, env) => PRD_AXES.map((axis) => {
  const hit = new Set(scenarios.map((s) => axis.of(s, env)));
  const universe = axis.universe.map((u) => u.id);
  const missing = universe.filter((u) => !hit.has(u));
  return {
    id: axis.id, label: axis.label,
    hit: hit.size, total: universe.length,
    ratio: universe.length ? hit.size / universe.length : 0,
    missingLevels: missing.map((m) => ({ id: m, label: axis.labelOf(m) })),
  };
});

/** Pairwise coverage: fraction of the (|A|×|B|) grid that has at least one
 *  scenario. Skips A === B. Returns one entry per unordered pair. */
export const pairwiseCoverage = (scenarios, env) => {
  const out = [];
  for (let i = 0; i < PRD_AXES.length; i += 1) {
    for (let j = i + 1; j < PRD_AXES.length; j += 1) {
      const a = PRD_AXES[i], b = PRD_AXES[j];
      const filled = new Set();
      scenarios.forEach((s) => filled.add(`${a.of(s, env)}|${b.of(s, env)}`));
      const total = a.universe.length * b.universe.length;
      out.push({
        pair: `${a.id}×${b.id}`, aLabel: a.label, bLabel: b.label,
        filled: filled.size, total,
        ratio: total ? filled.size / total : 0,
      });
    }
  }
  return out;
};

/** Forced rare-catastrophic cells (AC-9.9). Returns each critical overlay
 *  with whether at least one scenario carries it. */
export const forcedCellsCoverage = (scenarios) => {
  const present = new Set(scenarios.map((s) => overlayOf(s)));
  return FORCED_O_CELLS.map((id) => ({
    id, label: O_TYPES.find((o) => o.id === id)?.label || id,
    present: present.has(id),
  }));
};

/** Build the pairwise matrix for a specific axis pair — for the heatmap
 *  the CoverageMatrix component still renders. */
export const buildPrdMatrix = (scenarios, env, rowAxisId, colAxisId) => {
  const rowAxis = getPrdAxis(rowAxisId);
  const colAxis = getPrdAxis(colAxisId);
  const rowKeys = rowAxis.universe.map((u) => u.id);
  const colKeys = colAxis.universe.map((u) => u.id);
  const counts = {};
  scenarios.forEach((s) => {
    const key = `${rowAxis.of(s, env)}|${colAxis.of(s, env)}`;
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

/* Validation status per scenario (AC-9.13 / AC-9.16 / AC-9.17).
   Every scenario is either admitted-and-runnable or quarantined-with-
   reason. Predicates are deliberately narrow — a scenario is only
   quarantined when the compiler could NOT check it, not when a
   descriptive field happens to be short. Most well-formed scenarios
   should read as admitted. */
const QUARANTINE_REASONS = [
  {
    /* Explicitly marked invalid — the strongest signal. Used by
       generation stubs, tests, and cases the compiler rejects. */
    predicate: (s) => s.admitted === false || (s.name || "").includes("__invalid") || s.quarantined === true,
    reason: (s) => s.quarantineReason || "Marked invalid by the compiler.",
  },
  {
    /* Both a reference outcome AND sub-goals are missing — this is
       the "there is literally nothing to grade against" case, which
       is a real AC-9.13 violation. Missing just one is fine (the
       other still gives the verifier a target). */
    predicate: (s) => {
      const hasOutcome = !!(s.idealOutcome || s.expected);
      const hasChecks = Array.isArray(s.subGoals) && s.subGoals.length > 0;
      return !hasOutcome && !hasChecks;
    },
    reason: () => "No reference outcome AND no sub-goals — solvability can't be proven.",
  },
];
export const admissionOf = (scenario) => {
  for (const q of QUARANTINE_REASONS) {
    if (q.predicate(scenario)) {
      const reason = typeof q.reason === "function" ? q.reason(scenario) : q.reason;
      return { admitted: false, reason };
    }
  }
  return { admitted: true, reason: null };
};
export const admissionCounts = (scenarios) => {
  let admitted = 0; let quarantined = 0;
  scenarios.forEach((s) => { admissionOf(s).admitted ? (admitted += 1) : (quarantined += 1); });
  return { admitted, quarantined, total: scenarios.length };
};
