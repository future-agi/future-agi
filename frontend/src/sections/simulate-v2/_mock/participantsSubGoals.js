/*
  Which participant a failed sub-goal came from (design 9, 16 Nov).

  Nikhil's plan, "Test a multi-agent application": the point is to "identify
  which component caused an incorrect result", with worker-specific checks
  where supported; "missing attribution is Unknown, not guessed" (MA-2).

  So a failed sub-goal is put on a step only when its evidence points there:
    · the judge's evidence is a moment in the call (the line it failed) →
      the reply of the turn that moment falls in, said by the coordinator
    · a "Call <tool>" check → that tool call's step, or "never called"
  Anything else stays "Not tied to a step", with the reason.
*/
import { checkEvidenceOf } from "./scenarioSubGoals";

const NEAR_S = 2; // how far a judged moment may sit from a reply

/** The moment in the call of one of the trial's turns (its transcript row). */
const momentOf = (trial, transcript, idx) => {
  if (idx == null || !transcript?.length) return null;
  const step = trial?.steps?.[idx];
  const row =
    transcript.find((r) => r.id === step?.id) ||
    transcript.find((r) => r.id === `${trial?.id}-t${idx}`) ||
    transcript.find((r) => step?.text && r.content === step.text);
  return row
    ? { at: row.start_time_seconds, said: row.content || row.message }
    : null;
};

/**
 * The trial's failed and unsure sub-goals, each with the evidence the judge
 * gave: { key, text, mark, status, basis, at, said }. The critical one that
 * failed the call carries the moment it failed (the judge's evidence); a
 * missed supporting one carries none.
 */
export const subGoalFailuresOf = (trial, transcript, env, envState) => {
  if (!trial) return [];
  const { items = [] } = checkEvidenceOf(trial, env, envState) || {};
  const moment = momentOf(trial, transcript, trial.failStep);
  return items
    .filter((g) => g.status === "failed" || g.status === "inconclusive")
    .map((g) => {
      const crux = g.mark === "critical";
      return {
        key: g.key,
        text: g.text,
        mark: g.mark,
        status: g.status,
        basis: g.evidence,
        at: crux ? moment?.at ?? null : null,
        said: crux ? moment?.said ?? null : null,
      };
    });
};

/**
 * Each failure placed on the sequence: { ...failure, n, row, lane } where
 * row is "answer" (the reply of step n), "go" (step n itself) or "tool";
 * lane is the participant it came from. Unplaced ones keep n null and say
 * why in `untied`.
 */
export const placeFailures = (seq, failures = []) => {
  if (!seq) return [];
  const coordId = seq.lanes[1]?.id;
  const caller = seq.lanes[0]?.id;
  return failures.map((f) => {
    const call = /^Call ([a-z0-9_]+)/i.exec(f.text || "");
    if (call) {
      const tools = seq.steps.filter(
        (s) => s.kind === "tool" && s.tool === call[1],
      );
      const hit = tools.find((s) => s.bad) || tools[0];
      return hit
        ? { ...f, n: hit.n, row: "tool", lane: hit.from }
        : { ...f, n: null, untied: `${call[1]} was never called` };
    }
    if (f.at == null)
      return {
        ...f,
        n: null,
        untied: "The judge didn't point to a moment in the call",
      };
    /* The turn the judged moment falls in: the coordinator's reply. */
    const turns = seq.steps.filter(
      (s) =>
        s.kind === "message" &&
        ((s.from === caller && s.to === coordId) ||
          (s.from === coordId && s.to === caller)),
    );
    const replyAt = (s) => (s.from === caller ? s.answer?.at : s.at);
    const inTurn = turns.find(
      (s) =>
        s.at != null &&
        s.at <= f.at + 0.3 &&
        (replyAt(s) ?? Infinity) >= f.at - 0.3,
    );
    const nearest = turns
      .filter((s) => replyAt(s) != null)
      .map((s) => ({ s, d: Math.abs(replyAt(s) - f.at) }))
      .sort((a, b) => a.d - b.d)[0];
    const hit = inTurn || (nearest?.d <= NEAR_S ? nearest.s : null);
    if (!hit)
      return {
        ...f,
        n: null,
        untied: "The judged moment doesn't line up with a step",
      };
    return {
      ...f,
      n: hit.n,
      row: hit.from === caller ? "answer" : "go",
      lane: coordId,
    };
  });
};
