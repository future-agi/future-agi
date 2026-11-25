// How a metric's calculation reads to a person, from the states the server
// returns. Pure: the list, the header, History and the Overview share it.

/** A recalculation row in History carries its refresh attempt's state. An
 * attempt moves prepared → staging → verified → committed, or is aborted at
 * any step. Each maps to the one product status map (src/utils/runStatus). */
const ATTEMPT_STATES = {
  prepared: { status: "pending", label: "Preparing" },
  staging: { status: "running", label: "Calculating" },
  verified: { status: "running", label: "Finishing" },
  committed: { status: "committed", label: "Committed" },
  aborted: { status: "stopped", role: "warning", label: "Stopped" },
};

/** The badge for one recalculation attempt. An unknown state keeps its own
 * words (humanized by the status map), never a guessed meaning. */
export function attemptState(status) {
  return Object.hasOwn(ATTEMPT_STATES, status)
    ? ATTEMPT_STATES[status]
    : { status: status || "unavailable" };
}

/** The newest attempt on a History page, how many of the attempts on that
 * page stopped, and whether at least half of them did. Only what the page
 * holds; nothing is extrapolated. */
export function summarizeAttempts(results = []) {
  const attempts = results.filter((entry) => entry.kind === "recalculation");
  const stopped = attempts.filter((entry) => entry.status === "aborted").length;
  return {
    latest: attempts[0] || null,
    total: attempts.length,
    stopped,
    mostStopped: stopped > 0 && stopped * 2 >= attempts.length,
  };
}

/** Consecutive recalculation attempts in the same state collapse into one
 * row; every other History entry stays its own row, in order. */
export function groupHistoryRows(results = []) {
  const rows = [];
  for (const entry of results) {
    const last = rows.at(-1);
    if (
      entry.kind === "recalculation" &&
      last?.kind === "attempts" &&
      last.status === entry.status
    )
      last.entries.push(entry);
    else if (entry.kind === "recalculation")
      rows.push({ kind: "attempts", status: entry.status, entries: [entry] });
    else rows.push({ kind: "entry", entry });
  }
  return rows;
}

// One word per first-calculation state, shared by the header, the list, the
// Overview panel and History, so a state never reads two ways.
const FIRST_CALCULATION = {
  failed: { status: "failed", word: "failed" },
  running: { status: "running", word: "running" },
  pending: { status: "pending", word: "pending" },
};

/** The first-calculation state of a saved version, as one word: "pending"
 * (also when not reported), "running" or "failed". */
export function firstCalculationWord(draft) {
  return (
    FIRST_CALCULATION[draft?.backfill_status] || FIRST_CALCULATION.pending
  ).word;
}

/** A saved definition that has no active version yet. It is waiting for its
 * first calculation, not an unsaved draft. short is for a narrow column;
 * version: false leaves out the "vN · " prefix where the version is shown
 * next to it. */
export function firstCalculationBadge(
  draft,
  { short = false, version = true } = {},
) {
  const prefix =
    version && draft?.version != null ? `v${draft.version} · ` : "";
  const { status, word } =
    FIRST_CALCULATION[draft?.backfill_status] || FIRST_CALCULATION.pending;
  return {
    status,
    label: `${prefix}${
      short
        ? `${word.charAt(0).toUpperCase()}${word.slice(1)}`
        : `First calculation ${word}`
    }`,
  };
}

/** Whether a pending metric is worth checking again: it has no active
 * definition and its first calculation has neither failed nor finished. */
export function awaitsFirstCalculation(metric) {
  const draft = metric?.draft_definition;
  return Boolean(
    metric &&
      !metric.definition &&
      draft &&
      !["failed", "complete"].includes(draft.backfill_status),
  );
}
