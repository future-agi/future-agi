/**
 * Error localization for a simulated call's failed evals — the same shape the
 * dataset / observe localizer writes onto an eval cell, so the shared
 * EvalErrorLocalization → ErrorLocalizeCard path renders it unchanged:
 *
 *   {
 *     selected_input_key: "Call transcript",
 *     input_data:  { "Call transcript": "Agent: …\nCustomer: …" },
 *     input_types: { "Call transcript": "text" },
 *     error_analysis: { "Call transcript": [{ orgSen: { startIdx, endIdx, text }, weight, reason, improvement, rank_reason }] },
 *   }
 *
 * The prototype has no localizer backend, so the spans are picked from the
 * transcript by what each eval checks — commitments for policy evals, the
 * unresolved close for task-success evals. Deterministic per task + eval.
 */

const INPUT_KEY = "Call transcript";

const hash = (str) => {
  let h = 0;
  for (let i = 0; i < str.length; i += 1) h = ((h << 5) - h + str.charCodeAt(i)) | 0;
  return Math.abs(h);
};

const KINDS = {
  policy: {
    match: /polic|complian|adheren|rule|guardrail|pii|safety/i,
    signal: /refund|approv|exception|waive|promise|guarantee|credit|process (it|that|this)|go ahead|of course|no problem|i('| wi)ll|today|right away/i,
    reason: [
      "The agent committed to this before running the checks the policy requires, so the promise goes further than the policy allows.",
      "Builds on an earlier unchecked commitment — the agent keeps moving forward without confirming the policy conditions.",
      "A softer slip: the wording implies an outcome the agent hasn't verified yet.",
    ],
    improvement: [
      "Check the policy conditions with a tool call (return window, excluded items, approval limits) before agreeing, and tell the caller what's being checked.",
      "Pause the flow here and confirm eligibility before continuing — say plainly what still has to be verified.",
      "Phrase it as conditional (\"once I've confirmed…\") until the check has actually run.",
    ],
  },
  task: {
    match: /task|success|goal|resolut|outcome|complet/i,
    signal: /sorry|unable|can('|no)t|unfortunately|transfer|escalat|anything else|call back|later|not sure/i,
    reason: [
      "The conversation moved on here without the caller's goal being met, leaving the request unresolved.",
      "The agent lost the thread of the caller's goal at this turn, which set up the unresolved ending.",
      "A small drift away from the caller's goal — not the failure itself, but it made the close harder.",
    ],
    improvement: [
      "Before closing, confirm the caller's goal is actually done: state the outcome, the next step, and anything the caller still has to do.",
      "Restate the caller's goal back to them here, then take the next concrete step toward it.",
      "Keep this turn anchored to the goal — answer, then steer back to what the caller asked for.",
    ],
  },
  generic: {
    match: /.*/,
    signal: /refund|approv|promise|sorry|unable|transfer|confirm/i,
    reason: [
      "This is the turn where the evaluation's criteria stopped being met.",
      "This turn contributes to the failure alongside the top-ranked one.",
      "A weaker signal against the evaluation's criteria — worth checking once the others are fixed.",
    ],
    improvement: [
      "Rework this turn so it satisfies the evaluation's criteria, then re-run the eval to confirm.",
      "Adjust this turn after fixing the top-ranked one, then re-run the eval.",
      "Tighten the wording here so it clearly meets the criteria.",
    ],
  },
};

/* Softer wording for evals that passed but still lost points. */
const MINOR = {
  reason: [
    "This turn cost the most points — the agent handled it, but not as cleanly as the criteria expect.",
    "A small drift here: the turn is acceptable but misses part of what the evaluation checks for.",
    "Minor: the wording could be tighter, though it didn't change the outcome.",
  ],
  improvement: [
    "Tighten this turn so it fully meets the criteria — be explicit about the step being taken.",
    "Add what the evaluation looks for here, e.g. confirm or restate the caller's goal before moving on.",
    "Optional polish: shorter, more direct phrasing.",
  ],
};

const SEVERITY = [
  { level: "high", weight: 0.9, label: "High — the line most directly responsible for the failure." },
  { level: "medium", weight: 0.65, label: "Medium — contributes to the failure alongside the top span." },
  { level: "low", weight: 0.4, label: "Low — a weaker signal, worth checking once the others are fixed." },
];

export function transcriptText(task) {
  const lines = [];
  const agentSpans = [];
  let offset = 0;
  let turn = 0;
  let lastCustomer = null;
  (task?.steps || []).forEach((s) => {
    const text = String(s?.text || "").trim();
    if (!text || (s.role !== "agent" && s.role !== "customer")) return;
    turn += 1;
    const prefix = s.role === "agent" ? "Agent: " : "Customer: ";
    if (s.role === "agent") {
      agentSpans.push({
        start: offset + prefix.length,
        end: offset + prefix.length + text.length,
        text,
        turn,
        context: lastCustomer,
      });
    } else {
      lastCustomer = text;
    }
    lines.push(prefix + text);
    offset += prefix.length + text.length + 1;
  });
  return { text: lines.join("\n"), agentSpans };
}

/** Localization for one eval result on one task — every eval that lost any
 *  points gets one; a perfect score has nothing to point at. */
export function localizeEval(task, result) {
  const score = Number(result?.score ?? 0);
  if (!task || !result || score >= 1) return null;
  const { text, agentSpans } = transcriptText(task);
  if (!agentSpans.length) return null;

  const label = `${result.name || ""} ${result.id || ""}`;
  const kind = [KINDS.policy, KINDS.task].find((k) => k.match.test(label)) || KINDS.generic;
  const seed = hash(`${task.id}·${result.id}`);
  /* The more points lost, the more turns flagged and the more severe the top
     one: a failing 26% leads with High, a passing 97% gets a single Low. */
  const lost = 1 - score;
  const maxTurns = lost > 0.4 ? 3 : lost > 0.15 ? 2 : 1;
  const want = Math.min(maxTurns, 1 + (seed % 3));
  const topRank = score < 0.5 ? 0 : score < 0.8 ? 1 : 2;
  const verdict = result.passed ? "passed" : "failed";

  const scored = agentSpans.map((span, i) => ({
    span,
    i,
    score: (kind.signal.test(span.text) ? 10 : 0)
      + (kind === KINDS.task ? i / agentSpans.length : 0)
      + ((hash(`${seed}·${i}`) % 100) / 1000),
  }));
  const picked = scored
    .sort((a, b) => b.score - a.score)
    .slice(0, Math.min(want, agentSpans.length))
    .sort((a, b) => a.i - b.i);

  const ranked = [...picked].sort((a, b) => b.score - a.score);
  const segments = picked.map(({ span }) => {
    const position = Math.min(ranked.findIndex((p) => p.span === span), SEVERITY.length - 1);
    const sev = SEVERITY[Math.min(topRank + position, SEVERITY.length - 1)];
    const copy = result.passed ? MINOR : kind;
    return {
      unit_key: `${result.id}-${span.start}`,
      orgSen: { startIdx: span.start, endIdx: span.end, text: span.text },
      weight: sev.weight,
      reason: copy.reason[position],
      improvement: copy.improvement[position],
      rank_reason: sev.label,
      severity: sev.level,
      priority: position,
      verdict,
      /* Conversation localization: the flagged turn plus the customer line
         that prompted it, so the card can show the exchange, not a clip. */
      turn: { index: span.turn, role: "agent", text: span.text, context: span.context },
    };
  });

  return {
    selected_input_key: INPUT_KEY,
    input_data: { [INPUT_KEY]: text },
    input_types: { [INPUT_KEY]: "text" },
    error_analysis: { [INPUT_KEY]: segments },
    error_localizer_status: "completed",
  };
}
