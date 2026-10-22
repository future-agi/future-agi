/*
  Bring your own improver — the customer's coding agent proposes, Future AGI
  judges.

  Each time the coding agent wants proof it submits a candidate. We run the
  baseline and the candidate on the same scenarios, check the constraints and
  return a verdict. The agent sees per-scenario results only for the visible
  scenarios, and only pass or fail for the must-pass set. It iterates within
  a budget of attempts, then opens a PR carrying our proof.

  Mocked as a session that submits every few seconds once it starts, so the
  loop can be watched: two rejected attempts and one accepted, unless the
  budget runs out first. Everything is derived from the improvement record and
  the attempt number, so a reload shows the same attempts.
*/
import { hashSeed } from "./runStream";
import { OPT_STATUS, PER_CONVERSATION, REPEATS } from "./optimizationRuns";
import { codingAgentById, heldBackHiddenFrom, improverSessionFor } from "./integrations";

/* How many candidates one improvement accepts from an outside agent. */
export const ATTEMPT_BUDGET = 5;
/* Seconds between submissions in the prototype — a real agent takes minutes. */
const PACE = 8;

/*
  Where a candidate ran, and what we can stand behind for each — the trust
  tiers. Every proof names its tier.
*/
export const TIERS = {
  "future-agi": { label: "Run by Future AGI", note: "A pushed commit run in our sandbox — the full proof" },
  local: { label: "Run on your machine", note: "Your local code through our SDK, identified by commit and a fingerprint of uncommitted changes — our scenarios and scoring, the code identity is reported by your agent" },
  deployed: { label: "Deployed by you", note: "An endpoint or assistant we don't control — our scenarios and scoring, but we can't see what runs there" },
};

const sha = (s) => hashSeed(s).toString(16).padStart(8, "0").slice(0, 7);
const clampPct = (n) => Math.max(0, Math.min(100, Math.round(n)));
/* A visible scenario today's agent already passes (older records). */
const wasPassing = (t) => t.before === "passed" || !t.fromIssue;

/* What each attempt tried, and why the verdict went the way it did. */
const SCRIPT = [
  {
    title: "Rewrote the closing turn to read the tool result first",
    where: "local",
    dirty: true,
    reject: "mustPass",
    why: "A must-pass scenario regressed: the new closing turn skipped the identity check on a short call.",
  },
  {
    title: "Added a confirmation step and a second model call before closing",
    where: "local",
    dirty: false,
    reject: "cost",
    why: "Cost per conversation rose past the ceiling — the second model call runs on every turn.",
  },
  {
    title: "Gated the closing statement on the tool result, without the extra call",
    where: "future-agi",
    dirty: false,
    reject: null,
    why: null,
  },
];

/*
  A newer improvement's session (stamped `frozen.staticCheck`) also shows the
  gate at work: one attempt edits an evaluation file, and it is rejected
  before anything runs — never run, never charged.
*/
const SCRIPT_V2 = [
  SCRIPT[0],
  {
    title: "Loosened the Task success threshold and reworded the closing turn",
    where: "local",
    dirty: true,
    reject: "gate",
    files: ["evals/task_success.yaml", "prompts/system.md"],
    why: "Diff edits an evaluation file — rejected, not run, not charged.",
  },
  SCRIPT[1],
  SCRIPT[2],
];
/*
  Where an attempt ran — only a tier this agent actually has (candidateRoute's
  `tier`): an endpoint or assistant we don't control is Deployed by you; a
  cloud agent pushes commits we run; an agent connected through our SDK runs
  on your machine; code in a repository is a pushed commit we run. Without a
  known tier, the script's own order stands: local SDK first, then a commit.
*/
const whereOf = (session, agent, step, tier) => {
  if (session.where === "deployed" || tier === "deployed") return "deployed";
  if (agent.cloud) return "future-agi";
  if (tier === "local" || tier === "future-agi") return tier;
  return step.where;
};

/* The files an attempt's diff touches; the gate rejects any under scenarios/ or evals/. */
const JUDGE_PATH = /(^|\/)(scenarios|evals)\//;
export const gateOf = (files = []) => {
  const hit = files.find((f) => JUDGE_PATH.test(f));
  if (!hit) return null;
  return /(^|\/)evals\//.test(hit) ? `Diff edits an evaluation file (${hit}) — rejected, not run, not charged.` : `Diff edits a scenario file (${hit}) — rejected, not run, not charged.`;
};

/**
 * The attempts a session has submitted so far — the ones whose time has come.
 * Stops at the first accepted one, or at the attempt budget.
 */
export const attemptsOf = (record, session, { now = Date.now(), surface, budgetLeft = Infinity, tier } = {}) => {
  const r = record?.result;
  if (!session || !r) return [];
  if (record.frozen?.staticCheck) return attemptsV2(record, session, { now, surface, budgetLeft, tier });
  const started = Date.parse(session.startedAt);
  const agent = codingAgentById(session.agentId) || { label: "Your coding agent" };
  const visible = (r.trainMeasuredTasks || []).filter((t) => !t.critical);
  const mustPass = [...(r.trainMeasuredTasks || []), ...(r.heldTasks || [])].filter((t) => t.critical);
  const per = PER_CONVERSATION[surface === "voice" ? "voice" : "chat"];
  const ceiling = record.limits?.costCeiling ?? 0;
  const costLabel = ceiling <= 0 ? "Cost per conversation · no increase" : `Cost per conversation within +${ceiling}%`;
  /* Today's agent on the visible scenarios — what every attempt's change is measured from. */
  const before = visible.length ? clampPct((100 * visible.filter(wasPassing).length) / visible.length) : 0;

  const out = [];
  let spent = 0;
  for (let n = 1; n <= Math.min(ATTEMPT_BUDGET, SCRIPT.length); n += 1) {
    const at = started + n * PACE * 1000;
    if (at > now) break;
    const step = SCRIPT[n - 1];
    const key = `${record.id}:attempt:${n}`;
    /* Which visible scenarios this candidate moved. */
    /* An accepted attempt fixed what it was shown — at least the first
       failing scenario; a rejected one may have fixed some and broken more. */
    const perScenario = Object.fromEntries(visible.map((t, i) => {
      const was = wasPassing(t);
      const h = hashSeed(`${key}:${t.id}`) % 100;
      const fixed = !step.reject ? h < 70 || i === 0 : h < 40;
      return [t.id, fixed && !was ? "fixed" : was && h > 96 ? "broke" : fixed ? "fixed" : "same"];
    }));
    const mustFailed = step.reject === "mustPass" ? 1 : 0;
    /* Cost is checked only where it can be measured; elsewhere the second
       model call shows up as latency instead. */
    const costKnown = record.costMeasured !== false;
    const latencyCeiling = record.limits?.latencyCeiling ?? 10;
    const cost = step.reject === "cost" ? ceiling + 6 : Math.max(0, ceiling - 3);
    const latency = step.reject === "cost" && !costKnown ? latencyCeiling + 9 : 1;
    const checks = [
      { id: "model", label: "Model unchanged", pass: true },
      costKnown
        ? { id: "cost", label: costLabel, actual: `+${cost}%`, pass: cost <= ceiling }
        : { id: "cost", label: costLabel, actual: "unverified — no cost data", pass: null },
      { id: "latency", label: `Latency within +${latencyCeiling}%`, actual: `+${latency}%`, pass: latency <= latencyCeiling },
      { id: "protected", label: "Protected parts untouched", pass: true },
      { id: "judges", label: "No scenario or evaluation edited", pass: true },
    ];
    const conversations = (visible.length + mustPass.length + (r.heldMeasured || 0)) * REPEATS;
    const spend = Math.round(conversations * per);
    /* An attempt the budget can't pay for isn't run — the session stops there. */
    if (spent + spend > budgetLeft) {
      out.overBudget = true;
      break;
    }
    spent += spend;
    /* Scored the same way as the newer sessions and our own trials: the pass
       rate on the visible scenarios, its change against today's agent, and
       the held-back figure Future AGI scored and never sent. */
    const passingAfter = visible.filter((t) => perScenario[t.id] === "fixed" || (wasPassing(t) && perScenario[t.id] !== "broke")).length;
    const score = visible.length ? clampPct((100 * passingAfter) / visible.length) : 0;
    const hh = hashSeed(`${key}:held`);
    out.push({
      n,
      id: `${record.id}-a${n}`,
      improver: agent.label,
      label: `${agent.label} · attempt ${n}`,
      at: new Date(at).toISOString(),
      title: step.title,
      /* An endpoint or assistant we don't control is always "deployed by
         you"; otherwise local SDK runs first, then a pushed commit. */
      where: whereOf(session, agent, step, tier),
      commit: sha(key),
      fingerprint: step.dirty && whereOf(session, agent, step, tier) === "local" ? sha(`${key}:dirty`) : null,
      perScenario,
      visible: visible.map((t) => ({ id: t.id, title: t.title, outcome: perScenario[t.id] })),
      mustPass: { total: mustPass.length, failed: mustFailed },
      checks,
      accepted: !step.reject,
      why: step.reject === "cost" && !costKnown
        ? "Response time rose past the ceiling — the second model call adds a round trip on every turn."
        : step.why,
      score,
      before,
      delta: score - before,
      held: heldAfter(r.heldBase, hh, step.reject),
      spend,
    });
    if (!step.reject) break;
  }
  return out;
};

/* The held-back figure Future AGI scored: a rejected attempt barely moves
   it; an accepted one closes most of the gap. */
const heldAfter = (heldBase = 0, hh = 0, rejected = false) => clampPct(rejected
  ? heldBase + (hh % 7) - 3
  : heldBase + (100 - heldBase) * (0.55 + (hh % 30) / 100));

/*
  The newer session: must-pass is the engine's own three-part set (marked,
  regression memory, passing every repeat today); the visible scenarios are
  the target scenarios. Each attempt carries a score on the visible set, its
  change against today, and the held-back figure Future AGI scored and never
  sent. An attempt that edits a judge is rejected at the gate.
*/
const attemptsV2 = (record, session, { now, surface, budgetLeft, tier }) => {
  const r = record.result;
  const started = Date.parse(session.startedAt);
  const agent = codingAgentById(session.agentId) || { label: "Your coding agent" };
  const mustIds = new Set(r.mustPassIds || []);
  /* Newer records keep the must-pass set apart from the targets. */
  const all = [...(r.trainMeasuredTasks || []), ...(r.heldTasks || []), ...(r.mustPassTasks || [])];
  const mustPass = all.filter((t) => mustIds.has(t.id) || t.critical || t.remembered);
  const isMust = new Set(mustPass.map((t) => t.id));
  const visible = (r.trainMeasuredTasks || []).filter((t) => !isMust.has(t.id));
  const before = visible.length ? clampPct((100 * visible.filter((t) => t.flakyToday).length * 0.5) / visible.length) : 0;
  const per = PER_CONVERSATION[surface === "voice" ? "voice" : "chat"];
  const ceiling = record.limits?.costCeiling ?? 0;
  const latencyCeiling = record.limits?.latencyCeiling ?? 10;
  const costKnown = record.costMeasured !== false;
  const reps = r.repeats || REPEATS;
  const out = [];
  let spent = 0;
  for (let n = 1; n <= Math.min(ATTEMPT_BUDGET, SCRIPT_V2.length); n += 1) {
    const at = started + n * PACE * 1000;
    if (at > now) break;
    const step = SCRIPT_V2[n - 1];
    const key = `${record.id}:attempt:${n}`;
    const files = step.files || ["agent/prompts/system.md"];
    const gate = gateOf(files);
    const base = {
      n,
      id: `${record.id}-a${n}`,
      agentId: session.agentId || null,
      improver: agent.label,
      label: `${agent.label} · attempt ${n}`,
      at: new Date(at).toISOString(),
      title: step.title,
      where: whereOf(session, agent, step, tier),
      commit: sha(key),
      fingerprint: step.dirty && whereOf(session, agent, step, tier) === "local" ? sha(`${key}:dirty`) : null,
      files,
    };
    if (gate) {
      /* Rejected at the gate, before any run: nothing scored, nothing charged. */
      out.push({
        ...base,
        gate: true,
        perScenario: {},
        visible: visible.map((t) => ({ id: t.id, title: t.title, outcome: "not-run" })),
        mustPass: { total: mustPass.length, failed: 0, notRun: true },
        checks: [{ id: "judges", label: "No scenario or evaluation edited", actual: files.filter((f) => JUDGE_PATH.test(f)).join(", "), pass: false }],
        accepted: false,
        why: gate,
        score: null,
        delta: null,
        held: null,
        spend: 0,
      });
      continue;
    }
    const perScenario = Object.fromEntries(visible.map((t, i) => {
      const h = hashSeed(`${key}:${t.id}`) % 100;
      const fixed = !step.reject ? h < 70 || i === 0 : h < 40;
      return [t.id, fixed ? "fixed" : t.flakyToday && h > 90 ? "flaky" : "still-failing"];
    }));
    const mustFailed = step.reject === "mustPass" ? 1 : 0;
    const cost = step.reject === "cost" ? Math.max(ceiling, 0) + 6 : 0;
    const latency = step.reject === "cost" && !costKnown ? latencyCeiling + 9 : 1;
    const checks = [
      { id: "model", label: "Model unchanged", pass: true },
      costKnown
        ? { id: "cost", label: ceiling <= 0 ? "Cost per conversation · no increase" : `Cost per conversation within +${ceiling}%`, actual: `+${cost}%`, pass: cost <= Math.max(ceiling, 0) }
        : { id: "cost", label: ceiling <= 0 ? "Cost per conversation · no increase" : `Cost per conversation within +${ceiling}%`, actual: "unverified — no cost data", pass: null },
      { id: "latency", label: `Latency within +${latencyCeiling}%`, actual: `+${latency}%`, pass: latency <= latencyCeiling },
      { id: "protected", label: "Protected parts untouched", pass: true },
      { id: "judges", label: "No scenario or evaluation edited", pass: true },
    ];
    const conversations = (visible.length + mustPass.length + (r.heldMeasured || 0)) * reps;
    const spend = Math.round(conversations * per);
    if (spent + spend > budgetLeft) {
      out.overBudget = true;
      break;
    }
    spent += spend;
    const fixedN = Object.values(perScenario).filter((v) => v === "fixed").length;
    const score = visible.length ? clampPct((100 * fixedN) / visible.length) : 0;
    const hh = hashSeed(`${key}:held`);
    out.push({
      ...base,
      perScenario,
      visible: visible.map((t) => ({ id: t.id, title: t.title, outcome: perScenario[t.id] })),
      mustPass: { total: mustPass.length, failed: mustFailed },
      checks,
      accepted: !step.reject,
      why: step.reject === "cost" && !costKnown
        ? "Response time rose past the ceiling — the second model call adds a round trip on every turn."
        : step.why,
      score,
      before,
      delta: score - before,
      /* Scored by Future AGI on the held-back set, never sent to the agent. */
      held: heldAfter(r.heldBase, hh, step.reject),
      spend,
    });
    if (!step.reject) break;
  }
  return out;
};

/* Whether the session is still expecting another submission. */
export const sessionState = (session, attempts) => {
  if (!session) return null;
  const accepted = attempts.find((a) => a.accepted);
  if (accepted) return { key: "accepted", attempt: accepted };
  if (attempts.length >= ATTEMPT_BUDGET || attempts.overBudget) return { key: "budget", money: !!attempts.overBudget };
  return { key: "waiting" };
};

/** The proof a passing verdict produces, for the PR the coding agent opens. */
export const attemptProof = (record, attempt, name) => {
  const fixed = attempt.visible.filter((x) => x.outcome === "fixed").length;
  const broke = attempt.visible.filter((x) => x.outcome === "broke").length;
  return [
    `## Proof · ${attempt.label} · ${name || record.name} (${record.id})`,
    "",
    `**Verified by Future AGI — accepted.** Fixed ${fixed} of ${attempt.visible.length} visible scenarios, broke ${broke || "none"}; must-pass ${attempt.mustPass.total - attempt.mustPass.failed} of ${attempt.mustPass.total}.`,
    "",
    `Change: commit \`${attempt.commit}\`${attempt.fingerprint ? ` + uncommitted changes \`${attempt.fingerprint}\`` : ""} · ${TIERS[attempt.where]?.label}`,
    "",
    /* A workspace key could read the held-back set: say what the proof can promise. */
    ...(attempt.agentId !== undefined && !heldBackHiddenFrom(attempt.agentId)
      ? ["Held-back scenarios were readable by this agent — this proof promises integrity, not secrecy.", ""]
      : []),
    "### Constraints",
    ...attempt.checks.map((c) => `- ${c.pass === null ? "◌" : c.pass ? "✓" : "✗"} ${c.label}${c.actual ? ` — ${c.actual}` : ""}`),
    "",
    `_The starting version and the change ran on the same frozen scenarios and evaluations, each repeated ${REPEATS} times. The verdict was computed by Future AGI, not by ${attempt.improver}._`,
    `Full proof: https://app.futureagi.com/improvements/${record.id}/attempts/${attempt.n}`,
  ].join("\n");
};

/*
  An improvement the customer's coding agent fixes and Future AGI judges
  (RSI PRD, Bring your own improver) — started by handing the agent an Error
  Feed issue or a failed run. Today is measured and the scenarios are frozen
  as for any fix, but no search of ours runs: the agent's attempts are the
  candidates, each scored here. The measured sets stay on the record — the
  visible, must-pass and held-back scenarios every attempt is judged on.
*/
export const asCoderImprovement = (record, agent) => {
  const { pendingResult, pendingEnding: _ending, ...rest } = record;
  const result = record.result || pendingResult || null;
  return {
    ...rest,
    status: OPT_STATUS.COMPLETED,
    completedAt: new Date().toISOString(),
    improver: { agentId: agent.id, label: agent.label },
    result: result ? { ...result, trials: [], winner: null, finalists: [], pool: [], overruled: null } : result,
  };
};

/**
 * Where a coding agent's improvement stands, read from its session:
 * waiting for (or judging) attempts, one accepted, or out of attempts.
 * { key: "running" | "ready" | "no-fix", label, headline, attempt?, attempts }
 */
export const coderStatusOf = (record, envId, { now = Date.now(), surface, tier } = {}) => {
  const label = codingAgentById(record?.improver?.agentId)?.label || record?.improver?.label || "Your coding agent";
  const session = improverSessionFor(envId, record?.id);
  const attempts = session ? attemptsOf(record, session, { now, surface, budgetLeft: record.budget || Infinity, tier }) : [];
  const st = session ? sessionState(session, attempts) : { key: "waiting" };
  if (st.key === "accepted") {
    return {
      key: "ready", label: "Ready for review", attempt: st.attempt, attempts, improver: label,
      headline: `${st.attempt.label} accepted — ${st.attempt.visible.filter((x) => x.outcome === "fixed").length} of ${st.attempt.visible.length} scenarios fixed, must-pass held, every constraint met.`,
    };
  }
  if (st.key === "budget") {
    return {
      key: "no-fix", label: "No accepted attempt", attempts, improver: label,
      headline: st.money ? `${label} reached the budget without an accepted attempt.` : `${label} used all ${ATTEMPT_BUDGET} attempts without an accepted one.`,
    };
  }
  return {
    key: "running", label: attempts.length ? `${label} · attempt ${attempts.length + 1}` : `${label} is fixing`, attempts, improver: label,
    headline: attempts.length
      ? `${label} has submitted ${attempts.length} ${attempts.length === 1 ? "attempt" : "attempts"} — none accepted yet.`
      : `Waiting for ${label}'s first attempt.`,
  };
};
