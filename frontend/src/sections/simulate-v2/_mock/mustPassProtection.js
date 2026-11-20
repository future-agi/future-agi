/*
  What protects a scenario from an improvement — the must-pass set the
  improvement gate uses (RSI PRD): scenarios the user marked must-pass,
  scenarios added by regression memory, and every other scenario that passes
  all its repeats on the current version. Nothing else.

  The Scenarios tab says it out loud, so a set of hundreds doesn't look like it
  needs hand-marking: most of it is protected because it passes today. Marking
  is for what must never break, including what is still flaky.
*/

/*
  One scenario's protection, from its marks and its latest run (`latest`:
  { task, name } — the environment's latest run it was in).
    marked   · the user marked it, or wrote it must-pass
    memory   · kept from a released fix
    passing  · passed every repeat in the latest run
    open     · nothing holds it — it fails sometimes, or hasn't run
*/
export const protectionOf = (row, latest) => {
  if (!row || row.archived)
    return {
      id: "off",
      label: "Not run",
      line: "Archived — it doesn't run or count.",
    };
  if (row.regressionMemory) {
    return {
      id: "memory",
      label: "Regression memory",
      line: `Kept from ${row.regressionMemory.fromImprovement ? "" : "the fix for "}${row.regressionMemory.issueId} — an epoch that breaks it is rejected.`,
    };
  }
  if (row.critical) {
    return {
      id: "marked",
      label: "Must pass",
      line: "An epoch that breaks it is rejected.",
    };
  }
  const t = latest?.task;
  if (t && !t.pulled && t.status === "passed") {
    return {
      id: "passing",
      label: "Protected",
      line: `Passed every repeat in ${latest.name}, so an epoch can't break it. Mark it must-pass to keep it protected if it starts to fail.`,
    };
  }
  const why =
    !t || t.pulled
      ? "It hasn't been scored yet"
      : t.status === "flaky"
        ? `It passed only some repeats in ${latest.name}`
        : `It failed in ${latest.name}`;
  return {
    id: "open",
    label: "Not protected",
    line: `${why}, so an epoch may change how it does. Mark it must-pass if it must never break.`,
  };
};

/** The set's tally: how much of it improvements can't break, and why. */
export const protectionTally = (rows, latestOf) => {
  const out = {
    marked: 0,
    memory: 0,
    passing: 0,
    open: 0,
    total: 0,
    latestName: null,
  };
  (rows || []).forEach((r) => {
    /* Archived, or only proposed — not in the set until it's added. */
    if (r.archived || r.proposed) return;
    const latest = latestOf ? latestOf(r) : null;
    if (latest?.name && !out.latestName) out.latestName = latest.name;
    out[protectionOf(r, latest).id] += 1;
    out.total += 1;
  });
  return out;
};
