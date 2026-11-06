/*
  Trade-offs between candidates: quality against cost per conversation and
  latency. The search picks the best-scoring candidate that keeps every
  constraint; someone may prefer one a little lower that costs less or
  answers faster. The frontier is the set where nothing else is at least as
  good on all three and better on one.
*/
import { candidateEffect, costSavingsOf, limitGateOf, projectedHeld, chosenVerified, isIssueFix } from "../../_mock/optimizationRuns";

export { projectedHeld, chosenVerified };

export const candidatesOf = (record) => {
  const r = record?.result;
  if (!r) return [];
  /* The search's own gate: a percentage or a fixed amount, "No increase"
     within run-to-run noise, cost only where it's measured. */
  const gate = limitGateOf(record);
  return r.trials.map((t) => {
    const proposals = (r.pool || []).filter((p) => t.picks.includes(p.id));
    const effect = candidateEffect(t, proposals);
    const over = proposals.length ? t.overLimit || gate(proposals, costSavingsOf(t)) : null;
    const overCost = over?.id === "cost";
    const overLatency = over?.id === "latency";
    /* Stopped at Fits the environment — never ran, never eligible. */
    const unfit = (!!t.compat && t.compat.pass === false) || (!!t.static && t.static.pass === false);
    /* The same bar the verdict uses: a held-back gain inside run-to-run noise
       can't ship, so it can't be chosen either. (A fix is judged on its
       issue's scenarios; a held-back set already passing can only regress.) */
    const lift = (projectedHeld(r, t) ?? r.heldBase) - r.heldBase;
    const noisy = !isIssueFix(record) && r.heldBase < 100 && !r.split?.thin && lift <= (r.noise || 0);
    return {
      n: t.n,
      score: t.score,
      delta: t.delta,
      cost: effect.cost,
      latency: effect.latency,
      proposals,
      brokeBlocker: t.brokeBlocker,
      cheated: !!t.cheats,
      eligible: !t.brokeBlocker && !t.cheats && !(t.repoTests && !t.repoTests.pass) && !unfit && !overCost && !overLatency && !noisy,
      repoFailed: !!t.repoTests && !t.repoTests.pass,
      ...(unfit && { unfit: true }),
      why: t.cheats ? "failed a check" : t.brokeBlocker ? "a must-pass failure repeated" : unfit ? (t.static && t.static.pass === false ? "failed a static check" : "doesn't fit the simulated environment") : t.repoTests && !t.repoTests.pass ? "broke the repository's tests" : overCost ? "over the cost limit" : overLatency ? "over the latency limit" : noisy ? "held-back gain within run-to-run noise" : null,
    };
  });
};

const dominates = (a, b) => a.score >= b.score && a.cost <= b.cost && a.latency <= b.latency
  && (a.score > b.score || a.cost < b.cost || a.latency < b.latency);

/** Eligible candidates nothing else beats on quality, cost and latency at once. */
export const frontierOf = (candidates) => {
  const ok = candidates.filter((c) => c.eligible);
  /* One per distinct point — repeated trials of the same change land together. */
  const seen = new Set();
  return ok
    .filter((c) => !ok.some((o) => o !== c && dominates(o, c)))
    .filter((c) => {
      const k = `${c.score}:${c.cost}:${c.latency}`;
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    })
    .sort((a, b) => a.cost - b.cost || b.score - a.score);
};

/** What ships: the candidate someone chose on the trade-off view, else the search's pick. */
export const shippedCandidate = (record) => {
  const r = record?.result;
  if (!r?.winner) return null;
  const n = record.preferredTrial;
  if (!n || n === r.winner.n) return { ...r.winner, chosen: false, held: r.heldScore };
  const c = candidatesOf(record).find((x) => x.n === n);
  const t = r.trials.find((x) => x.n === n);
  /* Its held-back figure is a finalist's own, or projected until it's verified. */
  const finalist = (r.finalists || []).some((f) => f.n === n);
  return c && t
    ? { ...t, proposals: c.proposals, chosen: true, cost: c.cost, latency: c.latency, held: projectedHeld(r, c), verified: chosenVerified(record), projected: !finalist && !chosenVerified(record) }
    : { ...r.winner, chosen: false, held: r.heldScore };
};
