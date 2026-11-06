/*
  The run summary (PRD v7 · AC-12.9, 12.12, 12.15, 12.19–12.22, T58).

  The harness informs; the user decides. A finished run rolls up to a report —
  pass^1 and pass^k, pass and fail counts by verdict cause, and what the
  numbers don't cover — never to a verdict that blocks, promotes or ships an
  agent version. The user's own bar only colours the report.

  Two indicators, never one:

    execution health — did OUR harness do its job?  healthy | degraded | infra_failed
    run summary      — how did the AGENT do?        pass^1 · pass^k · causes

  pass^1 is the mean per-scenario pass share over scored scenarios (a 2-of-3
  scenario counts 2/3); pass^k is the share of scored scenarios that passed
  all k repeats (τ-bench). Scored = passed and failed (agent, required
  evaluation). Inconclusive, test invalid and error count neither for nor
  against the agent: left out of the rates, always counted. Scenarios pulled
  after testing invalid twice are outside every count; Outdated, Re-check and
  Stale scenarios ran but count toward no decision until updated or archived.
*/
import { OUTCOME_ORDER, VERDICT_CAUSES } from "./decoupled";
import { CAUSE_ORDER, STALE_LABEL, taskVerdictsOf } from "./runCauses";
import { PROOF_KINDS, TRUST_TIERS, proofKindOf } from "./rsiModel";
import { MIN_MEASURED } from "./runInsights";

/** The user's own bar — it colours the report and nothing else. */
export const REPORT_BAR_DEFAULT = { pass1: 0.8 };
export const reportBarOf = (envState) => ({ ...REPORT_BAR_DEFAULT, ...(envState?.reportBar || {}) });

/** Simulator failures above this share flag the result unreliable (AC-12.22). */
export const UNRELIABLE_SHARE = 0.1;

export const HEALTH_META = {
  healthy: { label: "Healthy", icon: "solar:server-square-linear" },
  degraded: { label: "Degraded", icon: "solar:server-square-linear" },
  infra_failed: { label: "Infra failed", icon: "solar:server-square-linear" },
};

const isMeasured = (t) => t?.status !== "unmeasured" && t?.measured !== false;
const isInfra = (t) => !!(t?.fault?.environment || t?.fault?.transport);
const pct = (x) => Math.round(x * 100);

const LOST_CAUSE = (t) => (t.fault?.environment && "environment")
  || (t.fault?.transport && "transport")
  || (t.fault?.simulator && "simulated caller")
  || (t.fault?.grading && "grading")
  || "unknown";

/**
 * Execution health — ours, not the agent's.
 * infra_failed when nothing could be graded or infrastructure lost half the
 * run; degraded when anything couldn't be graded; healthy otherwise.
 */
export const healthOf = (tasks = []) => {
  const total = tasks.length;
  const lost = tasks.filter((t) => !isMeasured(t));
  const infra = lost.filter(isInfra);
  const causes = [...lost.reduce((m, t) => m.set(LOST_CAUSE(t), (m.get(LOST_CAUSE(t)) || 0) + 1), new Map())]
    .sort((a, b) => b[1] - a[1])
    .map(([cause, n]) => `${cause} ${n}`);
  if (total && (lost.length === total || infra.length / total >= 0.5)) {
    return {
      id: "infra_failed",
      ...HEALTH_META.infra_failed,
      lost: lost.length,
      note: `${lost.length} of ${total} scenarios lost before a verdict (${causes.join(", ")}). Our failure, not the agent's.`,
    };
  }
  if (lost.length) {
    return {
      id: "degraded",
      ...HEALTH_META.degraded,
      lost: lost.length,
      note: `${lost.length} of ${total} scenarios couldn't be graded (${causes.join(", ")}). They're left out of the pass rate and counted by cause.`,
    };
  }
  return { id: "healthy", ...HEALTH_META.healthy, lost: 0, note: `All ${total} scenarios reached a verdict.` };
};

const shareOf = (t) => (typeof t?.passShare === "number" ? t.passShare : t?.status === "passed" ? 1 : 0);

/** "66% passed · 51% all 3 repeats", or fractions when there are too few to rate. */
export const summaryLineOf = (s) => {
  if (!s) return "";
  if (!s.scored) return "Nothing scored";
  if (s.lowVolume) return `${s.passedAll} of ${s.scored} passed${s.k > 1 ? ` all ${s.k}` : ""}`;
  return s.k > 1 ? `${s.pass1Pct}% passed · ${s.passKPct}% all ${s.k} repeats` : `${s.pass1Pct}% passed`;
};

/**
 * The summary for one run.
 *
 * `run` carries `tasks` (and `agentVersion`, `repeats`); `scenarioCount` is
 * what a full run would cover. Returns everything the header chip, Insights'
 * Run summary, the digest and the shared report read.
 */
export const runRollupOf = (run, { env, envState, scenarioCount } = {}) => {
  if (!run) return null;
  const tasks = run.tasks || [];
  const verdicts = taskVerdictsOf({ env, envState, run, tasks });
  const health = healthOf(tasks);
  const bar = reportBarOf(envState);

  /* Test invalid twice: pulled from the run and flagged for review. */
  const pulledTasks = tasks.filter((t) => verdicts.get(t.id)?.pulled);
  const outside = (v) => !v || v.pulled;
  /* Outdated / Re-check / Stale when the run started: ran, but count toward no decision. */
  const staleTasks = tasks.filter((t) => { const v = verdicts.get(t.id); return !outside(v) && v.stale; });
  /* Proposed candidates show separately. */
  const explorationTasks = tasks.filter((t) => { const v = verdicts.get(t.id); return !outside(v) && !v.stale && v.exploration; });
  const inScope = tasks.filter((t) => { const v = verdicts.get(t.id); return !outside(v) && !v.stale && !v.exploration; });

  const counts = Object.fromEntries(CAUSE_ORDER.map((c) => [c, 0]));
  const causeIds = Object.fromEntries(CAUSE_ORDER.map((c) => [c, []]));
  inScope.forEach((t) => {
    const c = verdicts.get(t.id).cause;
    counts[c] += 1;
    causeIds[c].push(t.id);
  });

  const scoredTasks = inScope.filter((t) => VERDICT_CAUSES[verdicts.get(t.id).cause]?.scored);
  const k = Math.max(1, run.repeats || run.trials || Math.max(0, ...scoredTasks.map((t) => t.repeats || 0)) || 1);
  const pass1 = scoredTasks.length ? scoredTasks.reduce((a, t) => a + shareOf(t), 0) / scoredTasks.length : 0;
  const passedAll = scoredTasks.filter((t) => shareOf(t) === 1).length;
  const passK = scoredTasks.length ? passedAll / scoredTasks.length : 0;
  const lowVolume = scoredTasks.length < MIN_MEASURED;

  /* Counts by outcome — passed · failed · inconclusive · test invalid · error. */
  const outcomes = Object.fromEntries(OUTCOME_ORDER.map((o) => [o, 0]));
  const outcomeIds = Object.fromEntries(OUTCOME_ORDER.map((o) => [o, []]));
  CAUSE_ORDER.forEach((c) => {
    const o = VERDICT_CAUSES[c]?.outcome || "inconclusive";
    outcomes[o] += counts[c];
    outcomeIds[o].push(...causeIds[c]);
  });
  /* Everything that counts neither for nor against the agent. */
  const inconclusive = outcomes.inconclusive + outcomes["test-invalid"] + outcomes.error;
  const simulatorShare = inScope.length ? counts["inc-simulator"] / inScope.length : 0;
  const unreliable = simulatorShare > UNRELIABLE_SHARE;

  /* What the pass rate leaves out, said once, beside it — in the run's own
     outcomes (passed · failed · inconclusive · test invalid · error). */
  const planned = Math.max((scenarioCount || 0) - explorationTasks.length - pulledTasks.length - staleTasks.length, inScope.length);
  const notRun = planned - inScope.length;
  const leftOut = [
    outcomes.inconclusive ? `${outcomes.inconclusive} inconclusive` : null,
    outcomes["test-invalid"] ? `${outcomes["test-invalid"]} test invalid` : null,
    outcomes.error ? `${outcomes.error} error` : null,
  ].filter(Boolean);
  const caveat = leftOut.length || notRun
    ? [
      leftOut.length ? `${leftOut.join(", ")} — ${inconclusive === 1 ? "it counts" : "they count"} neither for nor against the agent, so the pass rate leaves ${inconclusive === 1 ? "it" : "them"} out` : null,
      notRun ? `${notRun} ${notRun === 1 ? "scenario" : "scenarios"} not in this run` : null,
    ].filter(Boolean).join(". ").concat(".")
    : null;

  /* Retried once (new runs): what came back, and what didn't. */
  const retriedTasks = tasks.filter((t) => t.retried);
  const staleOf = (kind) => staleTasks.filter((t) => verdicts.get(t.id).stale === kind).length;
  const staleParts = Object.keys(STALE_LABEL).map((k) => [k, staleOf(k)]).filter(([, n]) => n);
  /* Cost and response times — recorded only when reported; the rest are unverified. */
  const speedTasks = tasks.filter((t) => isMeasured(t) && !verdicts.get(t.id)?.pulled);
  const recorded = speedTasks.some((t) => "costUnverified" in t || "responseMs" in t);
  const costKnown = speedTasks.filter((t) => !t.costUnverified);
  const respKnown = speedTasks.filter((t) => typeof t.responseMs === "number");
  const median = (xs) => {
    if (!xs.length) return null;
    const a = [...xs].sort((x, y) => x - y);
    return a[Math.floor((a.length - 1) / 2)];
  };

  const nameOf = (t) => String(t?.title || t?.name || t?.id || "").replace(/\.$/, "");
  const critical = scoredTasks.filter((t) => t.critical);
  const criticalFailed = critical.filter((t) => verdicts.get(t.id).cause !== "pass");

  const summary = {
    health,
    verdicts,
    bar,
    k,
    scored: scoredTasks.length,
    passed: counts.pass,
    failed: outcomes.failed,
    passedAll,
    pass1,
    passK,
    pass1Pct: pct(pass1),
    passKPct: pct(passK),
    lowVolume,
    counts,
    causeIds,
    outcomes,
    outcomeIds,
    inconclusive,
    simulatorShare,
    unreliable,
    belowBar: !lowVolume && scoredTasks.length > 0 && pass1 < bar.pass1,
    caveat,
    planned,
    notRun,
    pulled: {
      count: pulledTasks.length,
      ids: pulledTasks.map((t) => t.id),
      items: pulledTasks.map((t) => ({ id: t.id, title: nameOf(t), reason: verdicts.get(t.id).reason })),
    },
    stale: {
      count: staleTasks.length,
      ids: staleTasks.map((t) => t.id),
      outdated: staleOf("outdated"),
      recheck: staleOf("recheck"),
      stale: staleOf("stale"),
      line: staleTasks.length
        ? `${staleParts.map(([k, n]) => `${n} ${STALE_LABEL[k].toLowerCase()}`).join(" and ")} ${staleTasks.length === 1 ? "scenario" : "scenarios"} ran but count toward no decision until updated or archived`
        : null,
    },
    retried: {
      count: retriedTasks.length,
      recovered: retriedTasks.filter((t) => t.retried.recovered).length,
      ids: retriedTasks.map((t) => t.id),
    },
    speed: recorded ? {
      conversations: speedTasks.length,
      cost: Math.round(costKnown.reduce((a, t) => a + (t.cost || 0), 0) * 100) / 100,
      costUnverified: speedTasks.length - costKnown.length,
      responseMs: median(respKnown.map((t) => t.responseMs)),
      responseUnverified: speedTasks.length - respKnown.length,
    } : null,
    proofKind: runProofKindOf(run, envState),
    /* Which rule graded it, and what its sub-goals were checked against. */
    grading: gradingOf(run, envState),
    exploration: {
      count: explorationTasks.length,
      ids: explorationTasks.map((t) => t.id),
      candidates: explorationTasks.filter((t) => verdicts.get(t.id).candidate).length,
      passed: explorationTasks.filter((t) => verdicts.get(t.id).cause === "pass").length,
      failedIds: explorationTasks.filter((t) => verdicts.get(t.id).cause.startsWith("fail-")).map((t) => t.id),
      inconclusive: explorationTasks.filter((t) => verdicts.get(t.id).cause.startsWith("inc-")).length,
    },
    critical: { total: critical.length, failed: criticalFailed.map((t) => ({ id: t.id, title: nameOf(t) })) },
  };
  return { ...summary, line: summaryLineOf(summary) };
};

/**
 * Sealed · Live · Prompt only for a run: what it pinned (new runs), else read
 * from the environment's agent as it is now (`pinned: false`). Null without
 * an agent to read.
 */
export const runProofKindOf = (run, envState) => {
  const p = run?.proofKind;
  if (p?.kind && PROOF_KINDS[p.kind]) {
    return { kind: p.kind, tier: p.tier, ...PROOF_KINDS[p.kind], tierLabel: TRUST_TIERS[p.tier]?.label || null, pinned: true };
  }
  if (!envState?.agent) return null;
  return { ...proofKindOf(envState.agent), pinned: false };
};

/*
  How a run was graded ("Environments, defined").

    rule   2 — sub-goals decide pass or fail (pinned `gradingRule: 2`);
           0 — the older rule, where any failed evaluation failed the
               conversation and none meant no verdict. Old runs keep it.
    basis  what the sub-goals were checked against, from the proof kind the
           run pinned (else the agent as it is now): sealed runs check the
           world's end state and the agent's tool calls; live runs reach the
           customer's real systems, so only the starting state is checked and
           a model judges each sub-goal from the conversation.
*/
export const GRADING_RULES = {
  2: { label: "Sub-goals decide pass or fail", note: "A conversation passes when every critical sub-goal holds. A required evaluation that fails also fails it; other evaluations only score. Supporting sub-goals are reported and never fail it." },
  0: { label: "Graded by evaluations (older rule)", note: "This run is from before sub-goals decided pass or fail: a conversation failed when any evaluation failed, and had no verdict without one. It keeps the rule it ran under." },
};

export const GRADING_BASIS = {
  sealed: { judged: false, label: "Checked against end state and tool calls", note: "Simulated: tool calls were answered by the seeded world, so each sub-goal was checked against the world's end state and the agent's tool calls. A sub-goal about what was said is judged from the conversation." },
  live: { judged: true, label: "Judged from the conversation", note: "Live: the agent's tools reached your real systems, so only the starting state was checked and a model judged each sub-goal from the conversation." },
  "prompt-only": { judged: true, label: "Judged from the conversation", note: "Prompt only: the agent's tools weren't part of the run, so a model judged each sub-goal from the conversation." },
};

/** { rule, label, note, basis: { kind, judged, label, note } | null } for one run. */
export const gradingOf = (run, envState, { fresh = false } = {}) => {
  const rule = fresh || (run?.gradingRule || 0) >= 2 ? 2 : 0;
  const proof = run ? runProofKindOf(run, envState) : envState?.agent ? proofKindOf(envState.agent) : null;
  const basis = proof && GRADING_BASIS[proof.kind] ? { kind: proof.kind, ...GRADING_BASIS[proof.kind] } : null;
  return { rule, ...GRADING_RULES[rule], basis };
};

/** "Failed · agent 9 · Test invalid · simulator 1" — non-zero causes only. */
export const causeCountsLine = (s, { withPass = false } = {}) => CAUSE_ORDER
  .filter((c) => (withPass || c !== "pass") && s?.counts?.[c])
  .map((c) => `${VERDICT_CAUSES[c].label} ${s.counts[c]}`)
  .join(" · ");

/** What a run is read against — the run before it. */
// eslint-disable-next-line no-unused-vars
export const baselineFor = (summaries = [], runId, envState) => summaries.filter((r) => r.id !== runId).slice(-1)[0] || null;

/** Scenarios the suite holds today — what a full run would cover. */
export const suiteSizeOf = (envState) => (envState?.scenarios || []).filter((s) => !s.retired && !s.archived).length;

