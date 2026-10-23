/*
  Improvements metrics (RSI PRD, Phase 2): acceptance rate, time to
  proposal and fix survival at 30 days — read from the same rows the
  Improvements list shows, so the numbers and the table always agree.

    acceptance  finished improvements whose proposal someone accepted
                (shipped as a PR, a draft, a version) over every finished one
    time        median minutes from start to a proposal, for prompt-level and
                code-level changes separately (the PRD's targets differ)
    survival    released fixes still fixed after their 30-day watch, over
                every fix whose watch has ended (resolved + reopened)
*/
const median = (xs) => {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : Math.round((s[m - 1] + s[m]) / 2);
};

const PROPOSAL_ENDINGS = new Set(["ready", "stopped-early", "budget", "suggestion"]);

export const improvementMetricsOf = (rows = []) => {
  /* A run that failed for infrastructure reasons says nothing about the
     agent (its status is "failed" with nothing shipped); a reopened fix is
     also "failed" but has an output. */
  const finished = rows.filter((r) => r.status?.key !== "running" && !(r.status?.key === "failed" && !r.output));
  const accepted = finished.filter((r) => !!r.output);
  /* A stop at budget before searching proposed nothing (no change, so no changeKind). */
  const proposals = finished.filter((r) => !!r.output || (PROPOSAL_ENDINGS.has(r.status?.key) && (r.status.key !== "budget" || !!r.changeKind)));
  const minutesOf = (kind) => proposals.filter((r) => r.changeKind === kind && r.minutes != null).map((r) => r.minutes);
  const watchEnded = rows.filter((r) => r.output?.kind === "resolved" || r.output?.kind === "reopened");
  const survived = watchEnded.filter((r) => r.output?.kind === "resolved");
  return {
    acceptance: finished.length ? { rate: Math.round((accepted.length / finished.length) * 100), n: accepted.length, of: finished.length } : null,
    time: { prompt: median(minutesOf("prompt")), code: median(minutesOf("code")), n: proposals.length },
    survival: watchEnded.length ? { rate: Math.round((survived.length / watchEnded.length) * 100), n: survived.length, of: watchEnded.length } : null,
  };
};

export const minutesLabel = (m) => (m == null ? "—" : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60 ? `${m % 60} min` : ""}`.trim());
