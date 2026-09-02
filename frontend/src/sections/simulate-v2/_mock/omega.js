/**
 * Omega — the lens.
 *
 * The environment runs and scores. Omega reads a finished run and says what
 * went wrong, why, and what to change. Learning happens offline, from the runs
 * and the diagnoses. Those three stay apart on purpose: the game does not teach
 * you, it gives you a score; the coach reviews the replay; the practice happens
 * afterwards.
 *
 * The lens is a named panel of analyzers rather than one general-purpose
 * reviewer. That is deliberate. "The model looked at your run" is unfalsifiable
 * and unimprovable; six analyzers with stated inputs can each be argued with,
 * turned off, or shown to have missed something — and a customer can tell you
 * which one they trust.
 *
 * Every analyzer reads evidence the run already recorded. None of them are
 * evals: an eval scores an episode and feeds the reward, which is exactly what
 * a gaming detector must never do, or the next version learns to evade it.
 */

import { domainTally } from "./failures";
import { episodeReturn } from "./reward";
import { checklistSteps } from "./callDetail";

const SEV = { high: 3, medium: 2, low: 1 };

/* ── 1. failure classification ───────────────────────────────────────────── */

const classify = ({ tasks }) => {
  const tally = domainTally(tasks);
  if (!tally.length) return null;
  const agent = tally.find((t) => t.domain.id === "agent")?.count || 0;
  const ours = tally.filter((t) => !t.domain.measured).reduce((a, t) => a + t.count, 0);

  return {
    severity: ours > agent ? "high" : "low",
    /* `clear` is not the same as low severity. "Four failures, four distinct
       causes" is low-severity and still bad news; a green tick against it reads
       as an all-clear the run has not earned. Only an analyzer that found
       nothing wrong with its own subject says so. */
    clear: !ours,
    headline: ours
      ? `${agent} agent failures, ${ours} not the agent's`
      : `${agent} agent ${agent === 1 ? "failure" : "failures"}, nothing lost to the builder`,
    detail: ours
      ? `More than a third of what failed here was ours, not the agent's. Read the rate as ${agent} failures out of what could be measured, and fix the builder before drawing conclusions about the agent.`
      : "Every failure in this run is attributable to the agent, so the rate is a statement about the agent.",
    rows: tally.map((t) => ({
      label: t.domain.label,
      value: t.count,
      tone: t.domain.color,
      tasks: t.tasks?.map((x) => x.id) || [],
    })),
  };
};

/* ── 2. root cause clustering ────────────────────────────────────────────── */

const cluster = ({ failing }) => {
  if (!failing.length) return null;
  const groups = new Map();
  const add = (key, label, task, step) => {
    if (!groups.has(key)) groups.set(key, { label, tasks: [], step });
    groups.get(key).tasks.push(task);
  };

  failing.forEach((t) => {
    const missing = t.callLog?.missing || [];
    if (missing.length) missing.forEach((m) => add(`missing:${m}`, `never called ${m}`, t, m));
    else if (t.callLog?.unsupportedClaim) add("claim", "claimed something the call log does not support", t, "closing");
    else if (t.critical) add("rule", "broke a rule the environment enforces", t, "policy_check");
    else add("other", "failed a grader with no tool evidence either way", t);
  });

  const rows = [...groups.values()]
    .sort((a, b) => b.tasks.length - a.tasks.length)
    .map((g) => ({ label: g.label, value: g.tasks.length, tasks: g.tasks.map((t) => t.id), step: g.step }));

  return {
    severity: rows[0]?.value > 1 ? "medium" : "low",
    headline: `${failing.length} failures, ${rows.length} distinct ${rows.length === 1 ? "cause" : "causes"}`,
    detail: rows[0]?.value > 1
      ? `The largest cluster is ${rows[0].value} tasks that all ${rows[0].label}. One change addresses all of them — which is why the count of failures matters less than the count of causes.`
      : "Each failure has its own cause; there is no single change that fixes several at once.",
    rows,
  };
};

/* ── 3. reward hacking ───────────────────────────────────────────────────── */

/**
 * Gaming, detected from logs rather than scored.
 *
 * Kept out of the eval set on purpose: reward is the verifiers, so a gaming
 * detector added to them becomes an objective the agent optimises against —
 * the cheat detector handed to the cheater. Here it is a finding about the
 * measurement: these scores are inflated, and a check needs fixing, not a
 * prompt.
 *
 * Some signals are per-episode; the sharp ones are comparative, which is the
 * other reason this cannot be an eval — an eval sees one transcript.
 */
const gaming = ({ tasks, measured, env, baseline }) => {
  const signals = [];

  /* Said, not done: the transcript claims an outcome no tool call supports. */
  const claims = tasks.filter((t) => t.callLog?.unsupportedClaim);
  if (claims.length) {
    signals.push({
      id: "unsupported-claim",
      label: "Claimed an outcome with no tool call behind it",
      count: claims.length,
      why: "Every grader that reads words scores these as successes. The world says nothing happened.",
      tasks: claims.map((t) => t.id),
      step: "closing",
    });
  }

  /* Passed while a step the scenario required was never taken. The check that
     let it pass is the problem, not the agent. */
  const hollow = tasks.filter((t) => {
    if (t.status !== "passed") return false;
    const steps = checklistSteps(t, env, t);
    return steps.some((s) => s.tool && (t.callLog?.missing || []).includes(s.id));
  });
  if (hollow.length) {
    signals.push({
      id: "hollow-pass",
      label: "Passed without taking a step the scenario requires",
      count: hollow.length,
      why: "A check that passes when a required tool was never called cannot distinguish this run from an empty one.",
      tasks: hollow.map((t) => t.id),
    });
  }

  /* The efficiency term paid out on runs that finished early by doing less. */
  const short = tasks.filter((t) => {
    const ret = episodeReturn(t, { steps: checklistSteps(t, env, t) });
    return ret?.terms.some((x) => x.id === "efficiency") && (t.callLog?.calls || []).length <= 1;
  });
  if (short.length) {
    signals.push({
      id: "efficiency-exploit",
      label: "Collected the efficiency bonus by doing less, not by being faster",
      count: short.length,
      why: "Finishing under the reference length is rewarded. These runs got there by skipping work rather than by being efficient.",
      tasks: short.map((t) => t.id),
    });
  }

  /* Cross-run: the score went up while the agent touched the world less. This
     is the signal no per-episode eval can see. */
  if (baseline) {
    const callsNow = tasks.reduce((a, t) => a + (t.callLog?.calls?.length || 0), 0);
    const callsBefore = baseline.tasks.reduce((a, t) => a + (t.callLog?.calls?.length || 0), 0);
    if (measured.length && callsNow < callsBefore * 0.8 && (baseline.passRate || 0) < 100) {
      signals.push({
        id: "fewer-actions-higher-score",
        label: "Fewer tool calls than the baseline",
        count: callsBefore - callsNow,
        why: `This version made ${callsBefore - callsNow} fewer calls than run ${baseline.ordinal}. Worth checking that the score moved because it got better, not because it stopped trying.`,
        tasks: [],
      });
    }
  }

  if (!signals.length) {
    return {
      severity: "low",
      clear: true,
      headline: "No gaming signals",
      detail: "Every passing episode is supported by tool evidence, and no reward term paid out for doing less. This analyzer runs on every run and cannot be switched off.",
      rows: [],
    };
  }

  return {
    severity: signals.some((s) => s.id !== "fewer-actions-higher-score") ? "high" : "medium",
    headline: `${signals.length} gaming ${signals.length === 1 ? "signal" : "signals"}`,
    detail: "These scores are inflated. The fix is usually a check that cannot tell a real run from an empty one — not a prompt change.",
    rows: signals.map((s) => ({ label: s.label, value: s.count, note: s.why, tasks: s.tasks, step: s.step })),
    signals,
  };
};

/* ── 4. prompt alignment ─────────────────────────────────────────────────── */

const alignment = ({ env, failing }) => {
  const rules = env?.rules || [];
  if (!rules.length) return null;
  /* A rule the world enforces but the agent was never told is a rule the agent
     can only discover by breaking it. */
  const breached = rules.filter((r) => {
    const key = r.toLowerCase().match(/[a-z]{5,}/g) || [];
    return failing.some((t) => key.filter((w) => `${t.title} ${t.task}`.toLowerCase().includes(w)).length >= 2);
  });

  return {
    severity: breached.length ? "medium" : "low",
    clear: !breached.length,
    headline: breached.length
      ? `${breached.length} of ${rules.length} rules were broken in this run`
      : `All ${rules.length} rules held`,
    detail: breached.length
      ? "The environment enforces these; the prompt states them only in passing. An agent learns a rule it was never given by failing the scenario that tests it."
      : "Nothing the environment enforces was broken here.",
    rows: breached.map((r) => ({ label: r, value: null })),
  };
};

/* ── 5. grader trust ─────────────────────────────────────────────────────── */

const graderTrust = ({ tasks, measured }) => {
  const rows = [];
  const byEval = new Map();
  tasks.forEach((t) => (t.evalResults || []).forEach((r) => {
    if (!byEval.has(r.id)) byEval.set(r.id, { name: r.name, passed: 0, failed: 0, withEvidence: 0 });
    const e = byEval.get(r.id);
    if (r.passed) e.passed += 1; else e.failed += 1;
    if (r.passed && (t.callLog?.calls || []).length) e.withEvidence += 1;
  }));

  byEval.forEach((e) => {
    /* A grader that never fails is not measuring anything this suite contains. */
    if (e.failed === 0 && measured.length > 2) {
      rows.push({ label: `${e.name} passed everything`, value: null, note: "Never failed here — either the agent is strong on it or the check cannot fail." });
    }
    /* A grader that passes episodes with no tool evidence is reading words. */
    if (e.passed > 0 && e.withEvidence === 0) {
      rows.push({ label: `${e.name} passed with no tool evidence`, value: null, note: "Every pass came from an episode that called nothing." });
    }
  });

  return {
    severity: rows.length ? "medium" : "low",
    clear: !rows.length,
    headline: rows.length ? `${rows.length} ${rows.length === 1 ? "grader needs" : "graders need"} a second look` : "Graders discriminate",
    detail: rows.length
      ? "A check that cannot fail, or that passes an episode which did nothing, is not evidence. Fix the check before reading the score."
      : "Each grader both passed and failed episodes in this run, which is the minimum evidence that it measures something.",
    rows,
  };
};

/* ── 6. difficulty calibration ───────────────────────────────────────────── */

const difficulty = ({ measured }) => {
  if (!measured.length) return null;
  const bucket = (t) => (t.critical ? "Release blockers" : "Routine tasks");
  const groups = new Map();
  measured.forEach((t) => {
    const k = bucket(t);
    if (!groups.has(k)) groups.set(k, { total: 0, passed: 0 });
    const g = groups.get(k);
    g.total += 1;
    if (t.status === "passed") g.passed += 1;
  });

  const rows = [...groups.entries()].map(([label, g]) => ({
    label,
    value: `${Math.round((g.passed / g.total) * 100)}%`,
    note: `${g.passed} of ${g.total}`,
  }));
  const blockers = groups.get("Release blockers");

  const weak = blockers && blockers.passed / blockers.total < 0.6;
  return {
    severity: weak ? "high" : "low",
    clear: !weak,
    headline: weak
      ? "Fails hardest on the scenarios that block a release"
      : "No category is disproportionately weak",
    detail: "Where an agent fails matters more than how often. A suite that passes routine work and fails its blockers is worse than a lower rate spread evenly.",
    rows,
  };
};

/* ── the panel ───────────────────────────────────────────────────────────── */

export const ANALYZERS = [
  { id: "classification", label: "Failure classification", reads: "the five failure domains", run: classify },
  { id: "cluster", label: "Root-cause clustering", reads: "call logs, claims, rules, graders", run: cluster },
  { id: "gaming", label: "Reward hacking", reads: "claims vs tool evidence, reward terms, the baseline", run: gaming, always: true },
  { id: "alignment", label: "Prompt alignment", reads: "environment rules vs what failed", run: alignment },
  { id: "graders", label: "Grader trust", reads: "each check's pass/fail spread and its evidence", run: graderTrust },
  { id: "difficulty", label: "Difficulty calibration", reads: "blockers against routine scenarios", run: difficulty },
];

/**
 * Run the panel over one finished run.
 *
 * `summary` is the run being diagnosed; `baseline` is what it is being read
 * against, when there is one — the comparative analyzers need it and say so
 * rather than quietly returning nothing.
 */
export const omegaReport = ({ env, summary, baseline }) => {
  const tasks = summary?.tasks || [];
  const measured = tasks.filter((t) => t.status !== "unmeasured");
  const failing = measured.filter((t) => t.status === "failed" || t.status === "flaky");
  const ctx = { env, tasks, measured, failing, baseline, summary };

  return ANALYZERS.map((a) => {
    const result = a.run(ctx);
    return result ? { ...a, ...result, rank: SEV[result.severity] || 0 } : null;
  })
    .filter(Boolean)
    .sort((a, b) => b.rank - a.rank);
};

/** The one sentence the panel adds up to. */
export const omegaVerdict = (report, failing) => {
  const worst = report.find((r) => r.severity === "high");
  if (worst?.id === "gaming") return "The scores in this run are inflated. Fix the checks before changing the agent.";
  if (worst?.id === "classification") return "Most of what failed here was the builder. Re-run before reading anything into the agent's rate.";
  if (worst?.id === "difficulty") return "The agent is weakest exactly where a release is decided.";
  if (!failing.length) return "Nothing to fix — every measured scenario passed.";
  return "The failures cluster into a small number of causes, each with a change that addresses it.";
};

/**
 * The diagnosis as a trace, for the screen that runs it.
 *
 * The analyzers already know what they found; the running screen used to throw
 * that away and show six generic sentences instead, then delete itself. These
 * are the same analyzers with their findings attached, so the wait is spent
 * watching the diagnosis assemble — and the trace is kept afterwards, because
 * "how did it decide the scores were inflated" deserves an answer that outlives
 * the spinner.
 */
export const diagnosisTrace = ({ report, tasks, proposals = [], checks = [] }) => {
  const measured = tasks.filter((t) => t.status !== "unmeasured");
  const calls = tasks.reduce((a, t) => a + (t.callLog?.calls?.length || 0), 0);
  const steps = tasks.reduce((a, t) => a + (t.steps?.length || 0), 0);
  const claims = tasks.filter((t) => t.callLog?.unsupportedClaim).length;

  const TONE = { high: "#DC2626", medium: "#CA8A04", low: "text.secondary" };

  const read = {
    id: "evidence",
    label: "Reading the run's evidence",
    result: `${measured.length} of ${tasks.length} measurable`,
    lines: [
      `${tasks.length} episodes · ${steps} steps · ${calls} tool calls`,
      claims ? `${claims} closing statements with no tool call behind them` : "every closing statement has a tool call behind it",
      tasks.length - measured.length
        ? `${tasks.length - measured.length} episodes left out — nothing could be measured on them`
        : "nothing was lost to the builder",
    ],
  };

  const analyzers = report.map((a) => ({
    id: a.id,
    label: a.label,
    result: a.headline,
    tone: a.clear ? "#16A34A" : TONE[a.severity],
    /* Two rows is enough to show the working without turning the trace into
       the panel it is a preview of. */
    lines: (a.rows || []).slice(0, 2).map((r) => `${r.label}${r.value != null ? ` — ${r.value}` : ""}`),
  }));

  const draft = {
    id: "draft",
    label: "Drafting changes and projecting each one",
    result: `${proposals.length + checks.length} ${proposals.length + checks.length === 1 ? "change" : "changes"}`,
    lines: [
      ...(checks.length ? [`${checks.length} to the measurement, because the gaming analyzer fired`] : []),
      ...[...new Set(proposals.map((p) => p.kind))].map(
        (kind) => `${proposals.filter((p) => p.kind === kind).length} × ${kind.toLowerCase()}`,
      ),
    ],
  };

  return [read, ...analyzers, draft];
};
