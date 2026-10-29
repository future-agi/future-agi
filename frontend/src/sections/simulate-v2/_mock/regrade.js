/*
  Re-grade a past run (PRD v7 §4.10.3 · AC-12.23, AC-16.11).

  Runs store their traces and end-of-run world state, so when a sub-goal a
  run used gets a grader fix, or the eval version changes, the run can be
  graded again under the newer graders without re-simulating it. It is
  offered with a price and starts only on a click — never on its own.

  A policy change to a sub-goal is not a fix: it changes the criteria, so it
  shows as "criteria changed" in Compare and is not offered here.

  Mock: re-grading records `run.regradedWith = { subGoals, evalVersion, at,
  scenarioIds, label }`; the run then reads its verdicts under the newer
  graders — a few deterministic flips among the scenarios they touch.
*/
import { hashSeed } from "./runStream";
import { evalVersionOf, subGoalCatalogOf } from "./envModel";
import { estimateRegrade } from "./metering";
import { TOOL_CALL_JUDGE_ID } from "./evals";

const at = (s) => Date.parse(s || 0) || 0;
const shortName = (name) => String(name || "sub-goal").replace(/^(Holds the line: |Calls )/, "").split(/\s+/).slice(0, 3).join(" ").toLowerCase();

/**
 * What re-grading this run would mean right now, or null when nothing it
 * used has changed since it ran (or since it was last re-graded).
 * { subGoals: [{ id, name, v }], evalFrom, evalTo, scenarioIds, calls, credits, label }
 */
export const regradeOf = ({ env, envState, run, evals = [] }) => {
  if (!run?.finishedAt || run.pending) return null;
  const since = at(run.regradedWith?.at || run.finishedAt);
  const used = new Set(run.scenarioIds || (envState?.scenarios || []).map((r) => r.id));
  const edits = envState?.subGoalEdits || [];
  const catalog = subGoalCatalogOf(env, envState);

  /* Grader fixes after the run, on sub-goals its scenarios use. */
  const fixedIds = [...new Set(edits
    .filter((e) => at(e.at) > since && e.kind !== "policy")
    .map((e) => e.id))];
  const subGoals = fixedIds
    .map((id) => catalog.find((g) => g.id === id))
    .filter((g) => g && g.usedBy.some((sid) => used.has(sid)))
    .map((g) => ({
      id: g.id,
      name: g.name,
      kind: g.kind,
      /* Never edited in place: each fix is the next version (greeting@1 → @2). */
      v: 1 + edits.filter((e) => e.id === g.id && e.kind !== "policy").length,
      usedBy: g.usedBy.filter((sid) => used.has(sid)),
    }));

  const evalFrom = run.regradedWith?.evalVersion || run.evalVersion || null;
  const evalTo = evalVersionOf(envState);
  const evalChanged = !!evalFrom && evalFrom !== evalTo;
  if (!subGoals.length && !evalChanged) return null;

  const scenarioIds = evalChanged ? [...used] : [...new Set(subGoals.flatMap((g) => g.usedBy))];
  const calls = scenarioIds.length * Math.max(1, run.repeats || 1);
  const credits = estimateRegrade({ calls, judgedSubGoals: subGoals.filter((g) => g.kind === "judged").length, evals: evalChanged ? evals : [] });
  const parts = [
    ...subGoals.slice(0, 2).map((g) => `${shortName(g.name)}@${g.v}`),
    subGoals.length > 2 ? `+${subGoals.length - 2} more` : null,
    evalChanged ? `evals ${evalTo}` : null,
  ].filter(Boolean);
  return {
    subGoals,
    evalFrom,
    evalTo: evalChanged ? evalTo : null,
    /* Tool call evaluation is among the evals now: claims are read against the calls. */
    toolCalls: evals.some((e) => e?.id === TOOL_CALL_JUDGE_ID),
    scenarioIds,
    calls,
    credits,
    label: `Re-grade with ${parts.join(" / ")}`,
  };
};

/** The record a click on Re-grade writes onto the run. */
export const regradeRecordOf = (offer) => ({
  subGoals: offer.subGoals.map((g) => ({ id: g.id, v: g.v })),
  evalVersion: offer.evalTo || null,
  scenarioIds: offer.scenarioIds,
  label: offer.label.replace(/^Re-grade with /, ""),
  ...(offer.toolCalls && { toolCalls: true }),
  credits: offer.credits,
  at: new Date().toISOString(),
});

/**
 * The run's tasks as read under the graders it was re-graded with — a few
 * scenarios the fixed graders touch land the other way. Deterministic per
 * run, scenario and re-grade. Untouched when the run was never re-graded.
 */
export const withRegrade = (tasks = [], run) => {
  const rg = run?.regradedWith;
  if (!rg) return tasks;
  /*
    Re-graded with tool call evaluation (Debug failures → Fix Task success):
    every pass whose claim no tool call backs — or that skipped a tool the
    scenario requires — fails now. The conversations aren't re-run; the
    recorded calls are read against what was said.
  */
  if (rg.toolCalls) {
    return tasks.map((t) => {
      const hollow = t.status === "passed" && (t.callLog?.missing || []).length > 0;
      const said = !!t.callLog?.unsupportedClaim;
      if (!(said || hollow) || t.status === "unmeasured" || t.measured === false) return t;
      const reason = said
        ? "Fails under tool call evaluation — the claim has no tool call behind it."
        : "Fails under tool call evaluation — a tool the scenario requires was never called.";
      /* Every check that passed it on the words alone fails it now. */
      const flipped = (t.evalResults || []).filter((e) => e.passed).length;
      const evalResults = [
        ...(t.evalResults || []).map((e) => (e.passed ? { ...e, passed: false, score: Math.min(e.score ?? 1, 0.3), reason } : e)),
        { id: "tool_call_check", name: "Tool call evaluation", passed: false, score: 0, reason },
      ];
      const n = Math.max(1, t.samples?.length || t.repeats || 1);
      const wasFailed = t.status === "failed";
      return {
        ...t,
        evalResults,
        ...(!wasFailed && {
          status: "failed",
          verdict: "failed",
          samples: Array.from({ length: n }, () => "failed"),
          passes: 0,
          passShare: 0,
        }),
        /* changed = the verdict itself moved (pass or flaky → fail). */
        regraded: { with: rg.label, changed: !wasFailed, from: t.status, toolCalls: true, ...(flipped && { checksFlipped: flipped }) },
      };
    });
  }
  const touched = new Set(rg.scenarioIds || []);
  return tasks.map((t) => {
    if (!touched.has(t.id) || t.status === "unmeasured" || t.measured === false || t.cheat || t.canary) return t;
    if (hashSeed(`${run.id}:${t.id}:${rg.at}`) % 100 >= 12) return { ...t, regraded: { with: rg.label, changed: false } };
    const n = Math.max(1, t.samples?.length || t.repeats || 1);
    const toPass = t.status !== "passed";
    const evalResults = (t.evalResults || []).map((r, i) => (toPass
      ? { ...r, passed: true, score: Math.max(r.score ?? 0, 0.86), reason: `Passes under ${rg.label} — the old grader marked a valid path wrong.` }
      : i === 0 ? { ...r, passed: false, score: Math.min(r.score ?? 1, 0.42), reason: `Fails under ${rg.label} — the old grader let this through.` } : r));
    return {
      ...t,
      status: toPass ? "passed" : "failed",
      verdict: toPass ? "passed" : "failed",
      samples: Array.from({ length: n }, () => (toPass ? "passed" : "failed")),
      passes: toPass ? n : 0,
      passShare: toPass ? 1 : 0,
      evalResults,
      regraded: { with: rg.label, changed: true, from: t.status },
    };
  });
};
