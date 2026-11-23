/*
  Why a scenario is Not proved or Kept as written, in the backend's words.

  The backend sends one ready sentence per scenario (`row.stateReason`) —
  about 30 Not proved reasons and 9 Kept as written ones. It is shown
  verbatim; the fixed texts in scenarioStates.js are only the fallback for a
  row that carries none.

  The seeded store predates the field, so a few seeded rows read theirs from
  here, matched by the part of the id every seeded environment shares. Each
  entry is keyed by the state it explains: a reason never shows on a state
  it wasn't written for.
*/

const DEMO_REASONS = [
  {
    match: /-adversarial-0$/,
    kept: "Only an AI judge can grade whether the agent made things up, so no wrong-way example can be played against it.",
    notProved:
      "The wrong-way example passed its checks too, so these checks can't tell a made-up answer from a real one.",
  },
  {
    match: /-edge-0$/,
    kept: "Its lookups run inside the agent's own code, so there is nothing here to call them against.",
    notProved:
      "The reference solution needs a record the world no longer seeds, so it can't be staged.",
  },
  {
    match: /-rule-1-declined$/,
    kept: "Written before proofs existed and saved as it was — no reference solution has been played against it.",
    notProved:
      "The reference solution broke the rule it was written to show, so a pass here would mean nothing.",
  },
];

/** The ready sentence for this row in this state, or null. `kind` = "kept" | "notProved". */
export const stateReasonOf = (row, kind) => {
  if (typeof row?.stateReason === "string" && row.stateReason.trim())
    return row.stateReason.trim();
  const demo = DEMO_REASONS.find((d) => d.match.test(String(row?.id || "")));
  return demo?.[kind] || null;
};
