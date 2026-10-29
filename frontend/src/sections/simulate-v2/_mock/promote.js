/*
  Choose a winner (roadmap runs.md "Compare runs and choose a winner"): a
  must-pass case that regresses is listed first, whatever the average says.
  Promoting the candidate is an explicit, permitted step after review — the
  comparison alone deploys nothing — and the baseline stays one step away.
*/
import { currentUser } from "./scenarioProvenance";

const statusOf = (c) => c?.task?.status || c?.status || null;

/** Must-pass rows that passed in the baseline and fail in the last run. */
export const mustPassRegressionsOf = (rows = []) =>
  rows
    .filter((r) => r.critical && r.cells?.length > 1)
    .filter((r) => statusOf(r.cells[0]) === "passed" && ["failed", "flaky"].includes(statusOf(r.cells[r.cells.length - 1])))
    .map((r) => ({ id: r.id, title: r.title }));

/** The release a promotion records — with the review behind it. */
export const promotePatch = (envState, { version, runId, baseline, verdict, reviewed = [], note = "" }) => ({
  releases: [
    {
      version,
      runId,
      at: new Date().toISOString(),
      gate: "comparison",
      review: { by: currentUser().name, baseline, verdict, mustPassReviewed: reviewed, note },
    },
    ...(envState?.releases || []),
  ],
});
