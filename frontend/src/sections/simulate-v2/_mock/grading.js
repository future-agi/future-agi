/**
 * Grading, at three heights.
 *
 *   per turn      — did this exchange go wrong, and where exactly
 *   per journey   — did the whole task come out right
 *   release gate  — can this version ship
 *
 * The gate is the only one that answers a question anybody asks out loud. A
 * run that reports "82%" tells you nothing about whether to release; a run that
 * says "blocked: two critical scenarios failed" does.
 *
 * Evals and system metrics are the same ones the platform already has — the
 * gate composes them rather than inventing a parallel scoring system.
 */

/* ── the gate ────────────────────────────────────────────────────────────── */

export const GATE_KINDS = [
  { id: "critical", label: "Critical scenarios", unit: "% must pass" },
  { id: "overall", label: "All scenarios", unit: "% must pass" },
  { id: "eval", label: "Eval score", unit: "minimum average" },
  { id: "metric", label: "System metric", unit: "ceiling" },
];

export const defaultGate = () => ([
  { id: "g1", kind: "critical", label: "Critical scenarios pass", target: 100, actual: 100, unit: "%" },
  { id: "g2", kind: "overall", label: "All scenarios pass", target: 90, actual: 94, unit: "%" },
  { id: "g3", kind: "eval", label: "Policy adherence", target: 90, actual: 96, unit: "%" },
  { id: "g4", kind: "eval", label: "Task success", target: 85, actual: 91, unit: "%" },
  { id: "g5", kind: "metric", label: "p95 response latency", target: 3.0, actual: 2.4, unit: "s", ceiling: true },
  { id: "g6", kind: "metric", label: "Cost per run", target: 0.5, actual: 0.31, unit: "$", ceiling: true },
]);

export const gateVerdict = (rules) => {
  const failed = rules.filter((r) => (r.ceiling ? r.actual > r.target : r.actual < r.target));
  return { passed: failed.length === 0, failed };
};

/* ── system metrics ──────────────────────────────────────────────────────── */

/**
 * Observed, not tested. Load testing is an explicit non-goal, but latency has
 * to be visible or a gate cannot reference it.
 */
export const systemMetrics = () => [
  { id: "latency", label: "p95 latency", value: "2.4s", note: "first token to caller" },
  { id: "turns", label: "Median turns", value: "9", note: "per completed task" },
  { id: "cost", label: "Cost per run", value: "$0.31", note: "model + transcription" },
  { id: "interrupts", label: "Barge-ins handled", value: "87%", note: "recovered without repeating" },
];

/* ── per-turn scoring ────────────────────────────────────────────────────── */

const TURN_NOTES = [
  { speaker: "caller", text: "I want a refund on the jacket, it doesn't fit.", scores: { relevance: 1 } },
  { speaker: "agent", text: "Let me pull that up — can I take the phone number on the account?", scores: { policy: 1, tone: 0.95, grounded: 1 } },
  { speaker: "caller", text: "It's the one I'm calling from, obviously.", scores: { relevance: 1 } },
  { speaker: "agent", text: "Thanks. I can see order 44817. That's inside the return window.", scores: { policy: 1, tone: 0.9, grounded: 1 } },
  { speaker: "caller", text: "So when do I get my money?", scores: { relevance: 1 } },
  { speaker: "agent", text: "It'll be about five working days — I'll process that now.", scores: { policy: 0.4, tone: 0.9, grounded: 0.3 } },
  { speaker: "agent", text: "Refund issued to the card ending 4417.", scores: { policy: 1, tone: 1, grounded: 1 } },
];

/**
 * Turn-level scores for one task. Deterministic per task id so a trace opened
 * twice reads the same way.
 */
export const turnScores = (task) => {
  const seed = (task?.id || "").length;
  return TURN_NOTES.map((t, i) => ({
    ...t,
    index: i + 1,
    // the weak turn moves with the task, so every trace does not fail identically
    weak: t.speaker === "agent" && Math.min(...Object.values(t.scores)) < 0.6,
    at: `${String(Math.floor((i * 14 + seed) / 60)).padStart(2, "0")}:${String((i * 14 + seed) % 60).padStart(2, "0")}`,
  }));
};

export const TURN_DIMENSIONS = [
  { id: "policy", label: "Policy", help: "Did this turn stay inside the hard rules?" },
  { id: "grounded", label: "Grounded", help: "Was every claim in this turn backed by a tool result?" },
  { id: "tone", label: "Tone", help: "Did it sound like the brand, under pressure?" },
  { id: "relevance", label: "Relevance", help: "Caller turns are scored for whether the persona stayed in character." },
];
