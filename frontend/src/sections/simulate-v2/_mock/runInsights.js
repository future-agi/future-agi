/*
  A finished run, read for what to do next (PRD v6, Story 20).

  Everything here comes from graded evidence — scenarios that reached a
  verdict. A call that wasn't measured (the world didn't come up, the line
  dropped, the simulated caller went off script) has nothing to say about
  the agent, so it is left out of every number and counted by cause instead.

  Issues are clustered from the run's own failures: each failing scenario
  belongs to exactly one issue — its root failure, the first critical
  sub-goal it missed (else the tool it never called, the claim it couldn't
  back, the grader it fell under). What else went wrong in those scenarios is
  folded beneath as downstream symptoms, so one cause isn't counted three
  times. Issues stay with this environment and this run; they are not
  merged into the production Error Feed.
*/
import { subGoalResultsOf } from "./scenarioSubGoals";
import { hashSeed } from "./runStream";
import { VERDICT_CAUSES, causeOf } from "./decoupled";
import { CAUSE_ORDER, taskVerdictsOf } from "./runCauses";
import { goalLabel } from "./scenarios";

/* Below this many measured scenarios a share is noise — panels say so. */
export const MIN_MEASURED = 5;

const isMeasured = (t) => t?.status !== "unmeasured" && t?.measured !== false;
const isFail = (t) => t?.status === "failed" || t?.status === "error";
const shareOf = (t) => (typeof t?.passShare === "number" ? t.passShare : t?.status === "passed" ? 1 : 0);
const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;
const pctOf = (a, b) => (b ? Math.round((a / b) * 100) : null);

/* ── who the caller was ─────────────────────────────────────────────────── */

const TEMPERAMENTS = [
  ["hostile", /angry|scept|skept|tests boundaries|escalat|distrust|hostile|aggress|asks for proof|cites ticket|argu/i],
  ["rushed", /hurry|impatient|urgent|interrupt|terse/i],
  ["confused", /confus|non-native|vague|unsure|assumes context|changes scope|follow-up/i],
  ["easy-going", /polite|elderly|chatty|patient|detail|precise|audit|delegat|friendly/i],
];
/** One word for how the caller behaved — hostile, rushed, confused, easy-going. */
export const temperamentOf = (persona) => {
  const traits = (persona?.traits || []).join(" ");
  const hit = TEMPERAMENTS.find(([, re]) => re.test(traits));
  return hit ? hit[0] : null;
};

const topCount = (values) => {
  const m = new Map();
  values.filter(Boolean).forEach((v) => m.set(v, (m.get(v) || 0) + 1));
  return [...m.entries()].sort((a, b) => b[1] - a[1])[0] || null;
};

/* ── root failure ───────────────────────────────────────────────────────── */

/* "times out", "returns a 5xx"… — an injected fault, said plainly. */
const FAULT_SAID = { timeout: "times out", error: "returns an error", rate_limited: "is rate-limited", malformed: "returns a malformed response", slow: "is slow", after_commit: "commits, then times out before answering", permission: "denies permission", missing: "finds no such record", duplicate: "delivers an event twice", stale: "returns an out-of-date read", partial: "only partly succeeds" };

const rootOf = (t, sub) => {
  /* Lost to an injected fault: the fault is the trigger, not recovering is
     the agent's failure — the root, ahead of whatever sub-goal it took down. */
  if (t.faultFailed) {
    const hit = (t.toolFaults || []).find((h) => h.outcome === "failed") || (t.toolFaults || [])[0];
    if (hit) return { key: `fault:${hit.tool}:${hit.kind}`, kind: "fault", tool: hit.tool, fault: hit.kind };
  }
  const crux = sub.items.find((x) => x.mark === "critical" && x.status !== "met");
  if (crux) {
    const tool = /^call (\w+)/i.exec(crux.text)?.[1];
    return { key: `sg:${crux.id}`, kind: "subgoal", subGoal: { id: crux.id, text: crux.text, mark: crux.mark }, tool };
  }
  const miss = t.callLog?.missing?.[0];
  if (miss) return { key: `tool:${miss}`, kind: "tool", tool: miss };
  if (t.callLog?.unsupportedClaim) return { key: "claim", kind: "claim" };
  const ev = (t.evalResults || []).find((r) => !r.passed);
  if (ev) return { key: `eval:${ev.id}`, kind: "eval", eval: { id: ev.id, name: ev.name } };
  if (t.status === "error") return { key: "error", kind: "error" };
  return { key: "other", kind: "other" };
};

const short = (s, n = 64) => (s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s);
const lower = (s) => s.charAt(0).toLowerCase() + s.slice(1);
const an = (w) => `${/^[aeiou]/i.test(w) ? "an" : "a"} ${w}`;

/* What broke, said the way someone would report it. */
const titleOf = (root) => {
  if (root.kind === "subgoal") {
    const x = root.subGoal.text.replace(/\.$/, "");
    if (/^verify who/i.test(x)) return "Skips the identity check";
    if (root.tool) return `Calls ${root.tool} with the wrong arguments`;
    let m = /^holds the line:\s*(.+)/i.exec(x);
    if (m) return `Gives way on "${short(m[1], 56)}"`;
    if (/^refuse/i.test(x)) return "Doesn't refuse cleanly when a rule applies";
    m = /^confirm (.+)/i.exec(x);
    if (m) return `Doesn't confirm ${short(m[1], 56)}`;
    if (/^understand/i.test(x)) return "Misreads what the caller is asking for";
    if (/^recognise the manipulation/i.test(x)) return "Falls for the manipulation attempt";
    if (/^resist/i.test(x)) return `Doesn't ${lower(short(x, 60))}`;
    if (/^take the requested action/i.test(x)) return "Doesn't take the requested action";
    if (/^offer a specific next step/i.test(x)) return "Ends without a specific next step";
    if (/^check the request against the policy/i.test(x)) return "Acts before checking the policy";
    m = /^handle the (.+) part/i.exec(x);
    if (m) return `Falls through on the ${short(m[1], 40)} part`;
    return `Misses "${short(x, 56)}"`;
  }
  if (root.kind === "tool") return `Never calls ${root.tool}`;
  if (root.kind === "fault") return `Doesn't recover when ${root.tool} ${FAULT_SAID[root.fault] || "fails"}`;
  if (root.kind === "claim") return "Says it's done without doing it";
  if (root.kind === "eval") return `Falls under the ${root.eval.name} bar`;
  if (root.kind === "error") return "Errors out mid-conversation";
  return "Fails without one clear cause";
};

/* The root failure in a line, for the issue's header. */
const rootLineOf = (root) => {
  if (root.kind === "subgoal") return { label: root.subGoal.text, mark: root.subGoal.mark, note: "First critical sub-goal missed" };
  if (root.kind === "tool") return { label: `${root.tool} was needed and never called`, note: "No critical sub-goal recorded the miss" };
  if (root.kind === "fault") return { label: `${root.tool} ${FAULT_SAID[root.fault] || "failed"} (injected) and the agent didn't retry or escalate`, note: "Injected fault — the World tab's fault dial" };
  if (root.kind === "claim") return { label: "Told the caller something the call log doesn't support", note: "Said, not done" };
  if (root.kind === "eval") return { label: `${root.eval.name} below its threshold`, note: "Grader verdict" };
  if (root.kind === "error") return { label: "The episode errored before it finished", note: "Errored" };
  return { label: "No shared sub-goal, tool or grader", note: "Unclustered" };
};

/* A high-level fix, and the layer it lives in. */
const fixOf = (root) => {
  if (root.kind === "subgoal") {
    const x = root.subGoal.text;
    if (/^verify who/i.test(x)) return { text: "Make the identity check a gate: no account tool runs until the caller is verified, and pushback doesn't skip it.", layer: "Prompt + flow" };
    if (root.tool) return { text: `Spell out ${root.tool}'s required arguments in its tool description, and check them before the call goes out.`, layer: "Tool description" };
    const m = /^holds the line:\s*(.+)/i.exec(x);
    /* The issue's title already quotes the rule; the fix says what to do with it. */
    if (m) return { text: "State this rule in the system prompt with its refusal wording, and hold it when the caller pushes.", layer: "Prompt", rule: m[1] };
    if (/^refuse/i.test(x)) return { text: "Give the agent the refusal wording: one sentence of policy, then the alternative it can offer.", layer: "Prompt" };
    if (/^confirm/i.test(x)) return { text: `Add an explicit step before acting: ${lower(x.replace(/\.$/, ""))}.`, layer: "Prompt + flow" };
    return { text: `Make "${short(x, 70)}" an explicit step the agent takes, not one it can fall through.`, layer: "Prompt" };
  }
  if (root.kind === "fault") return { text: `When ${root.tool} fails, retry once, then tell the caller what's still pending and hand off — never report success the tool didn't return.`, layer: "Prompt + flow" };
  if (root.kind === "tool") return { text: `Say when ${root.tool} applies in its description, and don't let the closing turn run until it has returned.`, layer: "Tool description" };
  if (root.kind === "claim") return { text: "Only say something is done after the tool that does it returns success; otherwise say what's outstanding.", layer: "Prompt" };
  if (root.kind === "eval") return { text: `Before closing, check the answer against what ${root.eval.name} measures — say only what the tools returned.`, layer: "Prompt" };
  return { text: "Read the linked traces first — these failures don't share a cause yet.", layer: "Investigate" };
};

/*
  What kind of fix the root calls for — the one word a Fix's search reads to
  write its own first candidate: a rule to state, an identity gate, a tool's
  arguments, when a tool applies, a claim to back, a fault to recover from,
  a grader to answer, or a step to make explicit.
*/
const focusOf = (root) => {
  if (root.kind === "subgoal") {
    const x = root.subGoal.text;
    if (/^verify who/i.test(x)) return "identity";
    if (root.tool) return "tool-args";
    if (/^holds the line:/i.test(x)) return "rule";
    if (/^refuse/i.test(x)) return "refuse";
    return "step";
  }
  return { tool: "tool", fault: "fault", claim: "claim", eval: "eval" }[root.kind] || "other";
};

/**
 * An issue's root and fix, plain and serialisable — what a run-insights Fix
 * hands its search so the first candidates are about this issue rather than
 * whatever else failed in the run.
 */
export const issueRootFor = (issue) => {
  const root = issue?.root || {};
  const sub = root.subGoal?.text || null;
  return {
    kind: root.kind || "other",
    focus: focusOf(root.kind ? root : { kind: "other" }),
    subGoal: sub,
    rule: sub ? (/^holds the line:\s*(.+)/i.exec(sub.replace(/\.$/, ""))?.[1] || null) : null,
    tool: root.tool || null,
    fault: root.fault || null,
    faultSaid: root.fault ? FAULT_SAID[root.fault] || "fails" : null,
    eval: root.eval?.name || null,
    title: issue?.title || null,
    fix: issue?.fix?.text || null,
    layer: issue?.fix?.layer || null,
  };
};

/* ── issues ─────────────────────────────────────────────────────────────── */

const symptomsOf = (members, root, subs) => {
  const out = new Map();
  const add = (key, label, id) => {
    const s = out.get(key) || { key, label, ids: [] };
    if (!s.ids.includes(id)) s.ids.push(id);
    out.set(key, s);
  };
  members.forEach((t) => {
    if (root.kind !== "claim" && t.callLog?.unsupportedClaim) add("claim", "Said it was done without doing it", t.id);
    (t.callLog?.missing || []).forEach((name) => { if (!(root.kind === "tool" && root.tool === name)) add(`tool:${name}`, `Never called ${name}`, t.id); });
    (t.evalResults || []).filter((r) => !r.passed).forEach((r) => { if (!(root.kind === "eval" && root.eval.id === r.id)) add(`eval:${r.id}`, `${r.name} under its bar`, t.id); });
    (subs.get(t.id)?.items || []).filter((x) => x.mark !== "critical" && x.status === "failed")
      .forEach((x) => add(`sg:${x.id}`, `Supporting sub-goal missed: ${short(x.text, 60)}`, t.id));
    if (t.status === "flaky") add("mixed", "Passed some repeats and failed others", t.id);
  });
  return [...out.values()].sort((a, b) => b.ids.length - a.ids.length);
};

const impactOf = (t) => (1 - shareOf(t)) * (t.critical ? 2 : 1);

/**
 * Everything the Insights tab shows, for one run.
 * `goalOf(task)` names a scenario's goal the way the Scenarios table does.
 */
export const runInsightsOf = ({ tasks: allTasks = [], env, envState, run = null, goalOf = (t) => goalLabel(t.useCase) || "Other" }) => {
  /* Each scenario's verdict cause. */
  const verdicts = taskVerdictsOf({ env, envState, run, tasks: allTasks });
  /* Proposed candidates show separately. So are scenarios pulled after
     testing invalid twice, and Outdated / Re-check / Stale scenarios, which
     ran but count toward no decision. */
  const tasks = allTasks.filter((t) => { const v = verdicts.get(t.id); return v && !v.exploration && !v.pulled && !v.stale; });
  const outside = (key) => allTasks.filter((t) => verdicts.get(t.id)?.[key]).map((t) => t.id);
  const measured = tasks.filter(isMeasured);
  const unmeasured = tasks.filter((t) => !isMeasured(t));
  const passed = measured.filter((t) => t.status === "passed");
  const failed = measured.filter(isFail);
  const mixed = measured.filter((t) => t.status === "flaky");
  const lowVolume = measured.length < MIN_MEASURED;

  const subs = new Map(tasks.map((t) => [t.id, subGoalResultsOf(t, env, envState)]));

  /* Not counted, by verdict cause — inconclusive, test invalid, error. */
  const byCause = new Map();
  unmeasured.forEach((t) => {
    const k = verdicts.get(t.id)?.cause || causeOf(t);
    const row = byCause.get(k) || { id: k, label: VERDICT_CAUSES[k]?.label || k, ids: [] };
    row.ids.push(t.id);
    byCause.set(k, row);
  });
  /* Every cause, counted — fails included (AC-12.19). */
  const causes = CAUSE_ORDER.map((c) => ({ id: c, label: VERDICT_CAUSES[c].label, scored: VERDICT_CAUSES[c].scored, ids: tasks.filter((t) => verdicts.get(t.id)?.cause === c).map((t) => t.id) }))
    .filter((c) => c.ids.length);
  const notMeasured = { count: unmeasured.length, ids: unmeasured.map((t) => t.id), causes: [...byCause.values()].sort((a, b) => b.ids.length - a.ids.length) };

  /* ── clusters ── */
  const groups = new Map();
  [...failed, ...mixed].forEach((t) => {
    const root = rootOf(t, subs.get(t.id));
    const g = groups.get(root.key) || { root, members: [] };
    g.members.push(t);
    groups.set(root.key, g);
  });

  const toIssue = ({ root, members }) => {
    const sorted = [...members].sort((a, b) => (Number(isFail(b)) - Number(isFail(a))) || (Number(!!b.critical) - Number(!!a.critical)) || String(a.id).localeCompare(String(b.id)));
    const temps = members.map((t) => temperamentOf(t.persona));
    const top = topCount(temps);
    const pushback = top && top[0] === "hostile" && members.length >= 2 && top[1] / members.length >= 0.5;
    const critFails = members.reduce((a, t) => a + (subs.get(t.id)?.rollup?.criticalFailed || 0), 0);
    /* Critical scenarios that failed — counted, never a gate. */
    const blockers = members.filter((t) => t.critical && isFail(t)).length;
    const failedN = members.filter(isFail).length;
    /* Severity from critical sub-goal failures and counts. */
    const severity = (critFails >= 2 && failedN >= 2) || (critFails >= 1 && failedN >= 4)
      ? "critical"
      : critFails >= 1 || failedN >= 3 ? "high" : failedN ? "medium" : "low";
    const title = `${titleOf(root)}${pushback ? " under pushback" : ""}`;
    return {
      id: `RI-${(hashSeed(`${env?.id}:${root.key}`) % 900) + 100}`,
      key: root.key,
      title,
      severity,
      root: { ...root, ...rootLineOf(root), focus: focusOf(root) },
      memberIds: sorted.map((t) => t.id),
      members: sorted,
      failed: failedN,
      mixed: members.length - failedN,
      blockers,
      critFails,
      personas: top && top[1] >= 2 ? { temperament: top[0], count: top[1] } : null,
      symptoms: symptomsOf(members, root, subs),
      fix: fixOf(root),
      impact: Math.round(members.reduce((a, t) => a + impactOf(t), 0) * 100) / 100,
    };
  };

  const all = [...groups.values()].map(toIssue);
  /* A cause seen once isn't a pattern — unless it failed a critical scenario. */
  const issues = all.filter((i) => i.members.length >= 2 || i.blockers > 0)
    .sort((a, b) => b.impact - a.impact || b.members.length - a.members.length);
  const unclustered = all.filter((i) => !issues.includes(i)).flatMap((i) => i.members);
  const SEV = ["critical", "high", "medium", "low"];
  /* Severity first, impact within it — a low issue never ranks above a high one. */
  issues.sort((a, b) => SEV.indexOf(a.severity) - SEV.indexOf(b.severity) || b.impact - a.impact || b.members.length - a.members.length);
  issues.forEach((i, k) => {
    i.rank = k + 1;
    i.headline = [
      `${i.members.length} of ${measured.length} scenarios`,
      i.critFails ? plural(i.critFails, "critical sub-goal failure") : null,
      i.personas ? `personas: ${i.personas.temperament} × ${i.personas.count}` : null,
    ].filter(Boolean);
    i.severityRank = SEV.indexOf(i.severity);
  });

  /* ── evidence: the failing calls beside the nearest passing one ── */
  const family = (t) => String(t.id).replace(/-[^-]+$/, "");
  issues.forEach((i) => {
    const failing = i.members.slice(0, 3);
    const lead = failing[0];
    const score = (p) => (goalOf(p) === goalOf(lead) ? 3 : 0) + (family(p) === family(lead) ? 2 : 0)
      + (temperamentOf(p.persona) && temperamentOf(p.persona) !== temperamentOf(lead.persona) ? 0.5 : 0);
    const nearest = [...passed]
      .map((p) => [p, score(p), hashSeed(`${lead.id}:${p.id}`) % 97])
      .filter(([, s]) => s > 0)
      .sort((a, b) => b[1] - a[1] || a[2] - b[2])[0]?.[0] || null;
    i.evidence = { failing, passing: nearest, factors: nearest ? factorsOf(failing, nearest, i) : [] };
  });

  /* ── goals and sub-goals ── */
  const goalsMap = new Map();
  tasks.forEach((t) => {
    const g = goalOf(t);
    const row = goalsMap.get(g) || { goal: g, tasks: [] };
    row.tasks.push(t);
    goalsMap.set(g, row);
  });
  /* Sub-goal rows over a set of measured tasks: met / measured, and what
     didn't meet split into failed and inconclusive. */
  const subGoalRows = (m) => {
    const sg = new Map();
    m.forEach((t) => (subs.get(t.id)?.items || []).forEach((x) => {
      const r = sg.get(x.id) || { id: x.id, text: x.text, critical: false, measured: 0, met: 0, failed: 0, inconclusive: 0, ids: [], failingIds: [] };
      r.critical = r.critical || x.mark === "critical";
      r.measured += 1;
      r[x.status === "met" ? "met" : x.status === "failed" ? "failed" : "inconclusive"] += 1;
      if (!r.ids.includes(t.id)) r.ids.push(t.id);
      if (x.status !== "met" && !r.failingIds.includes(t.id)) r.failingIds.push(t.id);
      sg.set(x.id, r);
    }));
    return [...sg.values()]
      .map((r) => ({ ...r, pct: r.measured >= MIN_MEASURED ? pctOf(r.met, r.measured) : null, lowVolume: r.measured < MIN_MEASURED }))
      .sort(worstFirst((r) => r.met));
  };
  /* Worst first by the share that passed; with enough data ahead of without,
     then the bigger sample, then the most outright failures. */
  const worstFirst = (passOf) => (a, b) => {
    if (!a.measured !== !b.measured) return a.measured ? -1 : 1;
    const ra = passOf(a) / Math.max(1, a.measured);
    const rb = passOf(b) / Math.max(1, b.measured);
    return ra - rb || Number(b.critical || 0) - Number(a.critical || 0) || b.measured - a.measured || b.failed - a.failed;
  };

  const goals = [...goalsMap.values()].map(({ goal, tasks: ts }) => {
    const m = ts.filter(isMeasured);
    const p = m.filter((t) => t.status === "passed").length;
    return {
      goal,
      ids: ts.map((t) => t.id),
      failingIds: m.filter((t) => t.status !== "passed").map((t) => t.id),
      measured: m.length,
      passed: p,
      failed: m.filter(isFail).length,
      mixed: m.filter((t) => t.status === "flaky").length,
      unmeasured: ts.length - m.length,
      pct: m.length >= MIN_MEASURED ? pctOf(p, m.length) : null,
      lowVolume: m.length < MIN_MEASURED,
      critical: ts.some((t) => t.critical),
      subGoals: subGoalRows(m),
    };
  }).sort(worstFirst((g) => g.passed));
  /* The same sub-goals across the whole run — a shared definition is graded
     on every scenario that uses it, so its rate has a real sample. */
  const subGoals = subGoalRows(measured);

  return {
    total: tasks.length,
    measured: measured.length,
    passed: passed.length,
    failed: failed.length,
    mixed: mixed.length,
    passedIds: passed.map((t) => t.id),
    failedIds: failed.map((t) => t.id),
    mixedIds: mixed.map((t) => t.id),
    lowVolume,
    notMeasured,
    causes,
    exploration: { count: outside("exploration").length, ids: outside("exploration") },
    pulled: { count: outside("pulled").length, ids: outside("pulled") },
    stale: { count: outside("stale").length, ids: outside("stale") },
    issues,
    unclustered: { count: unclustered.length, ids: unclustered.map((t) => t.id) },
    goals,
    subGoals,
    brief: briefOf({
      run, measured, passed, failed, mixed, notMeasured, issues, lowVolume, unclustered, causes,
      exploration: outside("exploration"), pulled: outside("pulled"), stale: outside("stale"),
    }),
  };
};

/* What sets the failing calls apart from the passing one. */
function factorsOf(failing, pass, issue) {
  const out = [];
  const pt = temperamentOf(pass.persona);
  const ft = topCount(failing.map((t) => temperamentOf(t.persona)));
  if (ft && pt && ft[0] !== pt) out.push(`${an(ft[0])} caller on ${ft[1]} of ${failing.length} failing ${failing.length === 1 ? "call" : "calls"}, ${an(pt)} one on the passing call`);
  else if (failing.every((t) => t.persona?.name && t.persona.name === pass.persona?.name)) out.push("the same persona on both sides — the scenario made the difference, not the caller");
  const tool = issue.root.tool || (issue.root.kind === "tool" ? issue.root.tool : null);
  const missed = tool || failing.flatMap((t) => t.callLog?.missing || [])[0];
  if (missed && (pass.callLog?.calls || []).some((c) => c.name === missed)) out.push(`the passing call called ${missed}; the failing ones didn't`);
  const actors = failing.filter((t) => t.actor).length;
  if (actors && !pass.actor) out.push(`a third party joined ${actors} of ${failing.length} failing ${failing.length === 1 ? "call" : "calls"}`);
  const avg = Math.round(failing.reduce((a, t) => a + (t.steps || []).length, 0) / failing.length);
  const pl = (pass.steps || []).length;
  if (Math.abs(avg - pl) >= 2) out.push(`failing calls ran ${avg} turns against ${pl}`);
  if (failing.some((t) => t.critical) && !pass.critical) out.push("the failing scenarios are marked critical; the passing one isn't");
  return out.slice(0, 3);
}

/*
  The brief: what happened, what broke, what to fix first. Each part is
  text, or a link that filters the Scenarios tab ({ text, ids, label }) or
  jumps to an issue ({ text, issue }).
*/
function briefOf({ run, measured, passed, failed, mixed, notMeasured, issues, lowVolume, unclustered, causes = [], exploration = [], pulled = [], stale = [] }) {
  const runName = run?.ordinal ? `Run ${run.ordinal}` : "This run";
  const agent = run?.agentVersion ? ` put agent ${run.agentVersion} through` : " ran";
  const repeats = run?.repeats > 1 ? ` × ${run.repeats} repeats` : "";
  const happened = [{ text: `${runName}${agent} ${plural(measured.length + notMeasured.count, "scenario")}${repeats}. ` }];
  if (!measured.length) {
    happened.push({ text: "Nothing reached a verdict, so there is nothing to read about the agent yet." });
  } else {
    happened.push({ text: `${passed.length} of ${measured.length} measured`, ids: passed.map((t) => t.id), label: "Passed" });
    happened.push({ text: ` passed every repeat${lowVolume ? "" : ` (${pctOf(passed.length, measured.length)}%)`}` });
    if (failed.length) { happened.push({ text: ", " }); happened.push({ text: `${failed.length} failed`, ids: failed.map((t) => t.id), label: "Failed" }); }
    if (mixed.length) { happened.push({ text: ", " }); happened.push({ text: `${mixed.length} ${mixed.length === 1 ? "was" : "were"} mixed`, ids: mixed.map((t) => t.id), label: "Mixed" }); }
    happened.push({ text: "." });
  }
  /* The fails, by cause. */
  const fails = causes.filter((c) => c.id.startsWith("fail-"));
  if (fails.length && (fails.length > 1 || fails[0].id !== "fail-agent")) {
    happened.push({ text: " By cause: " });
    fails.forEach((c, k) => {
      if (k) happened.push({ text: ", " });
      happened.push({ text: `${c.label} ${c.ids.length}`, ids: c.ids, label: c.label });
    });
    happened.push({ text: "." });
  }
  if (notMeasured.count) {
    happened.push({ text: " " });
    happened.push({ text: `${notMeasured.count} didn't count`, ids: notMeasured.ids, label: "Not counted" });
    happened.push({ text: ` (${notMeasured.causes.map((c) => `${c.label} ${c.ids.length}`).join(", ")}) — neither for nor against the agent, left out of the rate.` });
  }
  if (exploration.length) {
    happened.push({ text: " " });
    happened.push({ text: `${exploration.length} proposed ${exploration.length === 1 ? "scenario" : "scenarios"}`, ids: exploration, label: "Proposed" });
    happened.push({ text: " ran too — their results show separately in the run summary, outside these numbers." });
  }
  if (pulled.length) {
    happened.push({ text: " " });
    happened.push({ text: `${pulled.length} ${pulled.length === 1 ? "scenario was" : "scenarios were"} pulled`, ids: pulled, label: "Pulled for review" });
    happened.push({ text: " after testing invalid twice — flagged for review, outside every count." });
  }
  if (stale.length) {
    happened.push({ text: " " });
    happened.push({ text: `${stale.length} outdated, re-check or stale ${stale.length === 1 ? "scenario" : "scenarios"}`, ids: stale, label: "Outdated, re-check or stale" });
    happened.push({ text: " ran but count toward no decision until updated or archived." });
  }
  if (issues.length) {
    const covered = issues.reduce((a, i) => a + i.members.length, 0);
    const lead = issues[0];
    happened.push({ text: ` ${covered} of the ${failed.length + mixed.length} non-passing scenarios trace to ${plural(issues.length, "issue")}; the most serious, ` });
    happened.push({ text: lead.title, ids: lead.memberIds, label: lead.title });
    happened.push({ text: `, covers ${lead.members.length}.` });
  } else if (failed.length + mixed.length) {
    happened.push({ text: " The failures don't share a cause yet — each is its own." });
  }

  const broke = issues.map((i) => ({
    issue: i.id,
    severity: i.severity,
    parts: [
      { text: i.title, ids: i.memberIds, label: i.title },
      { text: ` — ${i.headline.join(" · ")}` },
    ],
  }));
  if (unclustered.length) {
    broke.push({
      issue: null,
      parts: [
        { text: `${unclustered.length} more`, ids: unclustered.map((t) => t.id), label: "Unclustered failures" },
        { text: ` ${unclustered.length === 1 ? "failure has" : "failures have"} a cause seen only once.` },
      ],
    });
  }

  /* The brief is a two-minute read: the top three, the rest are in Issues. */
  const fixFirst = issues.slice(0, 3).map((i) => ({
    issue: i.id,
    text: i.fix.text,
    layer: i.fix.layer,
    /* What broke already names the issue; this says how much. */
    sub: [`${i.members.length} ${i.members.length === 1 ? "scenario" : "scenarios"}`, i.blockers ? `${i.blockers} critical` : null].filter(Boolean).join(" · "),
  }));

  return { happened, broke, fixFirst, fixMore: Math.max(0, issues.length - 3), lowVolume };
}
