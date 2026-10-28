// Vel's scenario-state sample for a voice support line (his prototype's
// env-voice-support), copy without em dashes. Shown only with
// ?uiSample=review: the API has no review notices, quality gate or
// spot-check yet.

export const SAMPLE_ENV_LABEL = "v3";

// The toolbar's Needs review list, one entry per notice Vel stacked above
// the scenario table. `attention` counts toward the button's number.
export const SAMPLE_REVIEW = {
  changeReview: { outdated: 2, stale: 1, recheck: 3, reprovable: 3 },
  proposed: 4,
  improvements: 2,
};

// The quality gate's inputs (PRD v7 §4.10.9). The drawer derives the state,
// the reason and the spot-check figures from these and the answers given.
export const SAMPLE_QUALITY_GATE = {
  total: 68,
  admitted: 66,
  bar: 0.8,
  quarantined: [
    {
      id: "sc_refund_no_order",
      title: "Refund request with no order number",
      summary: "Caller can't find the confirmation email",
      reason:
        "Not solvable: the world has no order the caller could mean, so no agent can pass it.",
    },
    {
      id: "sc_cancel_after_ship",
      title: "Cancel an order that already shipped",
      summary: "Caller insists the parcel hasn't left",
      reason:
        "Vacuous: an agent that says nothing at all passes every check.",
    },
  ],
  coverage: {
    rules: 9,
    covered: 8,
    uncovered: ["Refunds over $500 need a supervisor"],
  },
};

// The spot-check sample. `checks` and `expected` are what Vel's validate()
// and asSentence() produce for each row, precomputed. `answer` is a verdict
// already given; `carriedFrom` marks one kept from an earlier suite version.
export const SAMPLE_SPOT_CHECK = [
  {
    id: "sc_late_parcel",
    title: "Parcel is three days late",
    summary: "Calm caller, wants a delivery date",
    task: "Find out where order 48213 is and when it will arrive.",
    expected: "The agent looks up the order and gives the carrier's new delivery date.",
    checks: ["Calls lookup order", "Calls shipment status", "States a date"],
    answer: { verdict: "right", carriedFrom: "v2", by: "Priya" },
  },
  {
    id: "sc_double_charge",
    title: "Charged twice for one order",
    summary: "Frustrated caller, mentions the bank",
    task: "Get the duplicate charge on order 51907 refunded.",
    expected: "The agent confirms the duplicate and issues one refund for the second charge.",
    checks: ["Calls lookup order", "Calls issue refund once", "No refund over the charge"],
    answer: { verdict: "right" },
  },
  {
    id: "sc_wrong_size",
    title: "Wrong size delivered",
    summary: "Caller wants an exchange, not a refund",
    task: "Swap the medium jacket on order 50112 for a large.",
    expected: "The agent starts an exchange and does not refund the order.",
    checks: ["Calls lookup order", "Calls start exchange", "Does not call issue refund"],
    answer: null,
  },
  {
    id: "sc_address_change",
    title: "Change the delivery address",
    summary: "Caller moved last week",
    task: "Send order 49870 to the new address instead.",
    expected: "The agent updates the address only if the order hasn't shipped.",
    checks: ["Calls shipment status", "Calls update address"],
    answer: null,
  },
  {
    id: "sc_angry_escalation",
    title: "Caller asks for a manager",
    summary: "Angry caller, third call this week",
    task: "Get a supervisor on the line about order 47702.",
    expected: "The agent offers a transfer and logs the escalation.",
    checks: ["Calls transfer to human", "Logs escalation"],
    answer: null,
  },
];

const pct = (x) => `${Math.round(x * 100)}%`;

const GATE_LABEL = {
  "not-ready": "Needs work",
  "spot-check": "Spot-check needed",
  ready: "Reviewed",
};

export const sampleAnswers = () =>
  Object.fromEntries(
    SAMPLE_SPOT_CHECK.filter((r) => r.answer).map((r) => [r.id, r.answer]),
  );

// Vel's suiteGateOf, over the sample: the state, why, and the spot-check
// figures, recomputed as verdicts are given.
export function sampleGateOf(answers = sampleAnswers()) {
  const g = SAMPLE_QUALITY_GATE;
  const sampled = SAMPLE_SPOT_CHECK.length;
  const given = SAMPLE_SPOT_CHECK.map((r) => answers[r.id]).filter(
    (a) => a?.verdict,
  );
  const answered = given.length;
  const agreed = given.filter((a) => a.verdict === "right").length;
  const carriedList = given.filter((a) => a.carriedFrom);
  const base = {
    ...g,
    sampled,
    answered,
    agreed,
    agreement: answered ? agreed / answered : null,
    carried: carriedList.length,
    carriedFrom: carriedList[0]?.carriedFrom || null,
  };
  const out = (state, reason) => ({
    ...base,
    state,
    label: GATE_LABEL[state],
    reason,
  });
  const q = g.quarantined.length;
  if (q)
    return out(
      "not-ready",
      `${q} set aside. ${q === 1 ? "It fails" : "They fail"} the quality gate. Rewrite or archive ${q === 1 ? "it" : "them"}.`,
    );
  const u = g.coverage.uncovered;
  if (u.length)
    return out(
      "not-ready",
      `${u.length} rule${u.length === 1 ? " has" : "s have"} no scenario that passes the gate: "${u[0]}"${u.length > 1 ? ` and ${u.length - 1} more` : ""}.`,
    );
  if (answered < sampled)
    return out(
      "spot-check",
      answered
        ? `${answered} of ${sampled} spot-checked. Mark the rest to finish.`
        : `Spot-check ${sampled} scenarios: a person agrees they're realistic and their checks are right.`,
    );
  if (agreed / sampled < g.bar)
    return out(
      "spot-check",
      `${pct(agreed / sampled)} agreement, below the ${pct(g.bar)} bar. Fix the ones marked not right, then check them again.`,
    );
  return out(
    "ready",
    `Every scenario passes the quality gate, every rule is covered, ${pct(agreed / sampled)} spot-check agreement.`,
  );
}
