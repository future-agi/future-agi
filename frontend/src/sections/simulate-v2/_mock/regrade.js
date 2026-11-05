/*
  Re-grade a past run (PRD v7 §4.10.3 · AC-12.23, AC-16.11).

  Runs store their traces and end-of-run world state, so when a sub-goal a
  run used gets a grader fix, or the eval version changes, the run can be
  graded again under the newer graders without re-simulating it. It is
  offered with a price and starts only on a click — never on its own.

  A policy change to a sub-goal is not a fix: it changes the criteria, so it
  shows as "criteria changed" in Compare and is not offered here.

  Re-evaluating keeps the earlier result (runs.md): each re-grade is appended
  to `run.regrades` — { subGoals, evalVersion, at, scenarioIds, label, summary,
  before } where `before` is the result it replaced, so the original is always
  one look away. `run.regradedWith` mirrors the latest, for older readers. A
  run whose end state wasn't saved (voice or platform runs without a world
  snapshot) re-grades the conversation checks only — never the state.

  Mock: the run then reads its verdicts under the latest graders — a few
  deterministic flips among the scenarios they touch.
*/
import { hashSeed } from "./runStream";
import { evalVersionOf, subGoalCatalogOf, isConnectOnly } from "./envModel";
import { proofKindOf } from "./rsiModel";
import { estimateRegrade } from "./metering";
import { TOOL_CALL_JUDGE_ID } from "./evals";

/** Every re-grade this run has had, oldest first (older runs hold just the one). */
export const regradesOf = (run) =>
  run?.regrades || (run?.regradedWith ? [run.regradedWith] : []);

/** The re-grade the run's verdicts currently read under, or null. */
export const latestRegradeOf = (run) => regradesOf(run).slice(-1)[0] || null;

/**
 * Whether the run kept its end-of-run world state. A voice or platform run
 * with no world snapshot (connect-only, or a live agent in the customer's
 * account) has only the conversation and tool calls to read again.
 */
export const endStateSavedOf = ({ env, envState, run }) => {
  if (run?.endStateSaved != null) return !!run.endStateSaved;
  if (isConnectOnly(env)) return false;
  if (env?.surface === "voice" && proofKindOf(envState?.agent).kind === "live")
    return false;
  return true;
};

/** The result as counts — what a re-grade replaces, kept beside it. */
export const resultCountsOf = (tasks = []) => ({
  passed: tasks.filter((t) => t.status === "passed").length,
  failed: tasks.filter((t) => t.status === "failed").length,
  flaky: tasks.filter((t) => t.status === "flaky").length,
  unmeasured: tasks.filter((t) => t.status === "unmeasured").length,
});

const at = (s) => Date.parse(s || 0) || 0;
const shortName = (name) =>
  String(name || "sub-goal")
    .replace(/^(Holds the line: |Calls )/, "")
    .split(/\s+/)
    .slice(0, 3)
    .join(" ")
    .toLowerCase();

/**
 * What re-grading this run would mean right now, or null when nothing it
 * used has changed since it ran (or since it was last re-graded).
 * { subGoals: [{ id, name, v }], evalFrom, evalTo, scenarioIds, calls, credits, label }
 */
export const regradeOf = ({ env, envState, run, evals = [] }) => {
  if (!run?.finishedAt || run.pending) return null;
  const last = latestRegradeOf(run);
  const since = at(last?.at || run.finishedAt);
  const used = new Set(
    run.scenarioIds || (envState?.scenarios || []).map((r) => r.id),
  );
  const edits = envState?.subGoalEdits || [];
  const catalog = subGoalCatalogOf(env, envState);

  /* Grader fixes after the run, on sub-goals its scenarios use. */
  const fixedIds = [
    ...new Set(
      edits
        .filter((e) => at(e.at) > since && e.kind !== "policy")
        .map((e) => e.id),
    ),
  ];
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

  const evalFrom = last?.evalVersion || run.evalVersion || null;
  const evalTo = evalVersionOf(envState);
  const evalChanged = !!evalFrom && evalFrom !== evalTo;
  if (!subGoals.length && !evalChanged) return null;

  const scenarioIds = evalChanged
    ? [...used]
    : [...new Set(subGoals.flatMap((g) => g.usedBy))];
  const calls = scenarioIds.length * Math.max(1, run.repeats || 1);
  const credits = estimateRegrade({
    calls,
    judgedSubGoals: subGoals.filter((g) => g.kind === "judged").length,
    evals: evalChanged ? evals : [],
  });
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
    /* False: only the conversation checks can be read again. */
    stateSaved: endStateSavedOf({ env, envState, run }),
    label: `Re-grade with ${parts.join(" / ")}`,
  };
};

/** The record a click on Re-grade adds to the run; `before` is the result it replaces. */
export const regradeRecordOf = (offer, { before } = {}) => ({
  subGoals: offer.subGoals.map((g) => ({ id: g.id, v: g.v })),
  evalVersion: offer.evalTo || null,
  scenarioIds: offer.scenarioIds,
  label: offer.label.replace(/^Re-grade with /, ""),
  ...(offer.toolCalls && { toolCalls: true }),
  ...(offer.stateSaved === false && { conversationOnly: true }),
  summary: `${offer.scenarioIds.length} ${offer.scenarioIds.length === 1 ? "scenario" : "scenarios"} read again${offer.stateSaved === false ? " · conversation checks only" : ""}`,
  ...(before && { before }),
  credits: offer.credits,
  at: new Date().toISOString(),
});

/** The run with a re-grade added to its history — earlier results stay. */
export const withRegradeRecord = (run, rec) => ({
  ...run,
  regrades: [...regradesOf(run), rec],
  regradedWith: rec,
});

/**
 * The run's tasks as read under the graders it was re-graded with — a few
 * scenarios the fixed graders touch land the other way. Deterministic per
 * run, scenario and re-grade. Untouched when the run was never re-graded.
 */
export const withRegrade = (tasks = [], run) => {
  const rg = latestRegradeOf(run);
  if (!rg) return tasks;
  /*
    Re-graded with tool call evaluation (Debug failures → Fix Task success):
    every pass whose claim no tool call backs — or that skipped a tool the
    scenario requires — fails now. The conversations aren't re-run; the
    recorded calls are read against what was said.
  */
  if (rg.toolCalls) {
    return tasks.map((t) => {
      const hollow =
        t.status === "passed" && (t.callLog?.missing || []).length > 0;
      const said = !!t.callLog?.unsupportedClaim;
      if (
        !(said || hollow) ||
        t.status === "unmeasured" ||
        t.measured === false
      )
        return t;
      const reason = said
        ? "Fails under tool call evaluation — the claim has no tool call behind it."
        : "Fails under tool call evaluation — a tool the scenario requires was never called.";
      /* Every check that passed it on the words alone fails it now. */
      const flipped = (t.evalResults || []).filter((e) => e.passed).length;
      const evalResults = [
        ...(t.evalResults || []).map((e) =>
          e.passed
            ? {
                ...e,
                passed: false,
                score: Math.min(e.score ?? 1, 0.3),
                reason,
              }
            : e,
        ),
        {
          id: "tool_call_check",
          name: "Tool call evaluation",
          passed: false,
          score: 0,
          reason,
        },
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
        regraded: {
          with: rg.label,
          changed: !wasFailed,
          from: t.status,
          toolCalls: true,
          ...(flipped && { checksFlipped: flipped }),
        },
      };
    });
  }
  const touched = new Set(rg.scenarioIds || []);
  return tasks.map((t) => {
    if (
      !touched.has(t.id) ||
      t.status === "unmeasured" ||
      t.measured === false ||
      t.cheat ||
      t.canary
    )
      return t;
    /* Mock: without the end state, only the conversation checks can move. */
    if (
      hashSeed(`${run.id}:${t.id}:${rg.at}`) % 100 >=
      (rg.conversationOnly ? 6 : 12)
    )
      return { ...t, regraded: { with: rg.label, changed: false } };
    const n = Math.max(1, t.samples?.length || t.repeats || 1);
    const toPass = t.status !== "passed";
    const evalResults = (t.evalResults || []).map((r, i) =>
      toPass
        ? {
            ...r,
            passed: true,
            score: Math.max(r.score ?? 0, 0.86),
            reason: `Passes under ${rg.label} — the old grader marked a valid path wrong.`,
          }
        : i === 0
          ? {
              ...r,
              passed: false,
              score: Math.min(r.score ?? 1, 0.42),
              reason: `Fails under ${rg.label} — the old grader let this through.`,
            }
          : r,
    );
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
