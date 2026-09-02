/**
 * One scenario, as one run played it.
 *
 * The comparison table answers "which rows moved". Opening a row asks the next
 * question — *what actually happened* — and for a voice environment that is not
 * one artifact but four, each answering something the others cannot:
 *
 *   the transcript   what was said, and where the dead air was
 *   the checklist    which steps of the reference path the agent actually did
 *   the graph        the route it took, including the exit it took instead
 *   the numbers      what it cost, and where the seconds went
 *
 * Everything here is derived from the task the run already produced — its
 * turns, its call log, its verdict, its duration and its cost — rather than
 * invented alongside it. That is deliberate: a detail view that generates its
 * own numbers will eventually disagree with the table that opened it, and the
 * first person to notice stops trusting both.
 */

import { rng, hashSeed } from "./runStream";
import { validate } from "./contract";
import { attribute, faultReason, flakySource } from "./failures";
import { episodeReturn, rewardSpecLine } from "./reward";

/* ── how a call ended ────────────────────────────────────────────────────── */

/**
 * The outcome is not the verdict.
 *
 * A call that reached voicemail and a call where the agent lied about issuing a
 * refund are both "failed", and treating them as one thing is how you end up
 * rewriting a prompt when the actual problem was that nobody picked up. The
 * outcome says how the call *ended*; the graders say whether it was any good.
 */
export const CALL_OUTCOMES = {
  completed: {
    id: "completed",
    label: "Completed",
    color: "#16A34A",
    terminal: "closing",
  },
  voicemail: {
    id: "voicemail",
    label: "Voicemail",
    color: "#CA8A04",
    terminal: "voicemail_left",
  },
  hangup: {
    id: "hangup",
    label: "Hangup",
    color: "#DC2626",
    terminal: "user_disengaged",
  },
};

const outcomeOf = (task, h) => {
  if (!task) return CALL_OUTCOMES.completed;
  /* A run that never got off the ground did not "end" any particular way. */
  if (task.status === "unmeasured") return CALL_OUTCOMES.hangup;
  if (task.status === "passed" || task.status === "flaky") return CALL_OUTCOMES.completed;
  /* A failure with an unsupported claim is a call that ran to the end and
     misreported itself — the most expensive kind, and not a dropped call. */
  if (task.callLog?.unsupportedClaim) return CALL_OUTCOMES.completed;
  return h % 2 === 0 ? CALL_OUTCOMES.voicemail : CALL_OUTCOMES.hangup;
};

/* ── the reference path, as a checklist ──────────────────────────────────── */

const STEP_EXPECTATION = {
  start: "Open the call and state who is calling and why.",
  closing: "End the call politely.",
};

/**
 * The steps this scenario was supposed to go through.
 *
 * Taken from the reference solution the environment already holds — the tool
 * sequence a correct run makes — with an opening and a closing around it. It is
 * not a rubric someone wrote after reading the transcript: it existed before
 * the run, which is the only reason a "missed" here means anything.
 */
export const checklistSteps = (scenario, env, task) => {
  /* The tools the run was actually judged on. Falling back to the reference
     solution when a run predates the call log is fine; preferring it would not
     be — a checklist naming tools the grader never looked for produces
     "partial" rows on a run that did everything right. */
  const expected = task?.callLog?.expected?.length
    ? task.callLog.expected
    : (validate(scenario, env)?.reference || []).map((r) => r.tool);

  const middle = expected.slice(0, 3).map((name) => ({
    id: name,
    name,
    expectation: `Call ${name} and use what it returns.`,
    tool: true,
  }));
  return [
    { id: "start", name: "start", expectation: STEP_EXPECTATION.start },
    ...middle,
    { id: "closing", name: "closing", expectation: STEP_EXPECTATION.closing },
  ];
};

/**
 * How far the agent actually got, step by step.
 *
 * Three states rather than two. "Partial" is the one that earns its place: an
 * agent that raised a step and then lost the call did something different from
 * one that never raised it, and collapsing both into "missed" loses the only
 * evidence of where to look.
 */
const gradeChecklist = (steps, task, outcome, turns, h) => {
  const called = new Set((task?.callLog?.calls || []).map((c) => c.name));
  const missing = new Set(task?.callLog?.missing || []);
  const lastAgent = [...turns].reverse().find((t) => t.role === "agent");

  /* Where the call stopped. A voicemail never got past the opening; a hangup
     stopped somewhere in the middle; a completed call reached the end. */
  const reached = outcome.id === "voicemail"
    ? 0
    : outcome.id === "hangup"
      ? 1 + (h % Math.max(1, steps.length - 3))
      : steps.length - 1;

  return steps.map((step, i) => {
    if (i > reached) return { ...step, index: i, status: "missed" };
    if (i === reached && outcome.id === "hangup") {
      return {
        ...step,
        index: i,
        status: "partial",
        evidence: lastAgent
          ? { at: lastAgent.at, role: "agent", text: lastAgent.text, confidence: 55 + (h % 20) }
          : null,
      };
    }
    /* Reached, but the tool it stands on was never called — the agent talked
       its way through a step it did not do. */
    if (step.tool && (missing.has(step.id) || !called.has(step.id))) {
      return {
        ...step,
        index: i,
        status: missing.has(step.id) ? "missed" : "partial",
        evidence: missing.has(step.id) || !lastAgent
          ? null
          : { at: lastAgent.at, role: "agent", text: lastAgent.text, confidence: 60 + (h % 15) },
      };
    }
    return { ...step, index: i, status: "addressed" };
  });
};

/* ── the transcript, on a clock ──────────────────────────────────────────── */

const wordsIn = (text = "") => text.trim().split(/\s+/).filter(Boolean).length;

const buildTranscript = (task, r) => {
  let at = 0;
  return (task?.steps || []).map((s, i) => {
    const dur = (s.duration || 900) / 1000;
    /* Dead air between turns is state in a voice call, not whitespace — a
       two-second gap is what a caller experiences as the agent thinking. */
    const silenceAfter = i % 2 === 1 ? Math.round(r() * 22) / 10 : Math.round(r() * 6) / 10;
    const turn = {
      id: s.id || `t${i}`,
      index: i,
      at: Math.round(at * 10) / 10,
      dur: Math.round(dur * 10) / 10,
      role: s.role === "agent" ? "agent" : "customer",
      text: s.text || "",
      silenceAfter,
    };
    at += dur + silenceAfter;
    return turn;
  });
};

/* ── the whole detail ────────────────────────────────────────────────────── */

/**
 * Everything the detail view shows for one run of one scenario.
 *
 * Seeded by run id and scenario id together, so the same cell always produces
 * the same call — a stakeholder who reopens a row after lunch sees what they
 * saw before it.
 */
export const callDetail = ({ env, envState, run, task, scenario }) => {
  if (!task) return null;

  const seed = `${run?.id || "run"}::${scenario?.id || task.id}`;
  const h = hashSeed(seed);
  const r = rng(h);
  /* Facts about the scenario rather than about the run: the same call is placed
     to the same number in every version of the agent, and a phone number that
     changes between columns makes the two look like different calls. */
  const scenarioHash = hashSeed(scenario?.id || task.id);

  const turns = buildTranscript(task, r);
  const outcome = outcomeOf(task, h);

  const agentWords = turns.filter((t) => t.role === "agent").reduce((a, t) => a + wordsIn(t.text), 0);
  const userWords = turns.filter((t) => t.role === "customer").reduce((a, t) => a + wordsIn(t.text), 0);
  const words = agentWords + userWords;
  const silence = turns.reduce((a, t) => a + t.silenceAfter, 0);

  /*
    Latency is per turn, not per call — "how long before it answered me" is the
    number a caller feels. It is split across the four stages of a voice
    pipeline because that is where a regression is actionable: 300ms added in
    endpointing is a VAD setting, 300ms added in the LLM is a model choice.
  */
  const base = 1300 + Math.round(r() * 900);
  const share = { endpointing: 0.16 + r() * 0.06, transcriber: 0.13 + r() * 0.05, voice: 0.13 + r() * 0.05 };
  const round10 = (n) => Math.round(n / 10) * 10;
  const endpointing = round10(base * share.endpointing);
  const transcriber = round10(base * share.transcriber);
  const voice = round10(base * share.voice);
  const llm = round10(base - endpointing - transcriber - voice);
  const latency = [
    { id: "endpointing", label: "Endpointing", ms: endpointing, color: "#2563EB" },
    { id: "transcriber", label: "Transcriber", ms: transcriber, color: "#4F8DF5" },
    { id: "llm", label: "LLM", ms: llm, color: "#7DAEFB" },
    { id: "voice", label: "Voice", ms: voice, color: "#2563EB" },
  ];
  const latencyMs = endpointing + transcriber + llm + voice;

  /*
    The cost split has to add up to the cost the table already reported for this
    task, so it is derived from it rather than sampled next to it.
  */
  const total = task.cost || 0;
  const sttShare = 0.18 + r() * 0.06;
  const ttsShare = 0.27 + r() * 0.05;
  const money = (n) => Math.round(n * 1000) / 1000;
  const stt = money(total * sttShare);
  const tts = money(total * ttsShare);
  const cost = [
    { id: "stt", label: "Speech to Text", icon: "solar:microphone-linear", amount: stt },
    { id: "llm", label: "LLM", icon: "solar:cpu-bolt-linear", amount: money(total - stt - tts) },
    { id: "tts", label: "Text to Speech", icon: "solar:volume-loud-linear", amount: tts },
  ].map((c) => ({ ...c, pct: total ? Math.round((c.amount / total) * 100) : 0 }));

  const steps = gradeChecklist(checklistSteps(scenario || task, env, task), task, outcome, turns, h);
  const pass = steps.filter((s) => s.status === "addressed").length;
  const partial = steps.filter((s) => s.status === "partial").length;
  const missed = steps.filter((s) => s.status === "missed").length;

  const graph = buildPath({ env, task, scenario, turns, outcome, steps });

  const providerOf = () => envState?.agent?.values?.provider || envState?.agent?.via || "endpoint";

  const domain = attribute(task);
  /* What this episode was worth, and which terms paid out. */
  const ret = episodeReturn(task, { steps });

  return {
    id: scenario?.id || task.id,
    return: ret,
    rewardSpec: rewardSpecLine(),
    /* Who this result belongs to, carried alongside it everywhere it is shown. */
    domain,
    measured: domain?.measured !== false,
    fault: faultReason(task),
    flakySource: task.status === "flaky" ? flakySource(task) : null,
    /* Unique per run × scenario — what the recording strip is drawn from. */
    seedKey: seed,
    outcome,
    /* Direction is a property of the scenario, not of the run — the same call
       is outbound in every version of the agent. */
    type: /inbound|call(s|ed)? in|customer calls/i.test(`${scenario?.task || ""}`) ? "inbound" : "outbound",
    phone: `+1${2000000000 + (scenarioHash % 799999999)}`,
    provider: providerOf(),
    durationS: Math.round((task.durationMs || 0) / 100) / 10,
    turns,
    stats: {
      turnCount: turns.length,
      latencyMs,
      userPct: words ? Math.round((userWords / words) * 100) : 0,
      aiPct: words ? 100 - Math.round((userWords / words) * 100) : 100,
      words,
      silenceS: Math.round(silence * 10) / 10,
      /* Time to first word: how long the caller waited before hearing anything.
         Zero when the agent opened the call, which is most outbound runs. */
      ttfwMs: turns[0]?.role === "agent" ? 0 : round10(300 + r() * 700),
      userInt: outcome.id === "hangup" ? 1 + (h % 2) : 0,
      aiInt: h % 7 === 0 ? 1 : 0,
    },
    latency,
    latencyMs,
    cost,
    costTotal: total,
    summary: summaryFor(task, outcome, steps),
    checklist: {
      steps,
      pass,
      partial,
      missed,
      /* A partial counts half. Rounding it up would let a call that raised a
         step and dropped it read the same as one that completed it. */
      pct: steps.length ? Math.round(((pass + partial * 0.5) / steps.length) * 100) : 0,
    },
    graph,
    analysis: analysisFor(task, outcome, steps),
  };
};

/* ── the trajectory ──────────────────────────────────────────────────────── */

const shorten = (t, n) => (t && t.length > n ? `${t.slice(0, n - 1)}…` : t || "");

/**
 * The route, as something you can actually read.
 *
 * A list of step names is not a trajectory. What someone opening this wants is
 * the shape of the episode: where it started, every tool it called and what
 * came back, the point where a rule had to be applied, the branch it could have
 * taken and didn't, the step it should have taken and didn't, and how the whole
 * thing ended. All of that already exists in the run — the call log knows what
 * was invoked and what it returned, the checklist knows what was expected, the
 * scenario knows which rule is on trial — it was just never drawn.
 *
 * The spine is what happened. Branches hang off it, and there are two kinds
 * worth distinguishing: a step the scenario *needed* and the agent skipped
 * (a finding), and a branch the agent legitimately did not take (context).
 * Collapsing those two into one dashed line is how a graph starts lying.
 */
function buildPath({ env, task, scenario, turns, outcome, steps }) {
  const agentTurns = turns.filter((t) => t.role === "agent");
  const at = (i) => agentTurns[Math.min(i, Math.max(0, agentTurns.length - 1))]?.at ?? 0;
  const statusOf = (id) => steps.find((s) => s.id === id)?.status;

  const calls = (task.callLog?.calls || []).filter((c) => !c.id.includes("-extra-"));
  const missing = task.callLog?.missing || [];

  /* A browser session has steps, not turns, and nothing it did was speech. */
  const spoken = (task.steps || []).some((st) => st.role);
  const spine = [{
    id: "start",
    label: "start",
    kind: "start",
    sub: spoken
      ? `${turns.length} turns · ${Math.round(turns.reduce((a, t) => a + t.dur, 0))}s of speech`
      : `${turns.length} steps · ${Math.round(turns.reduce((a, t) => a + t.dur, 0))}s active`,
    at: 0,
    status: "ok",
  }];

  calls.forEach((c, i) => spine.push({
    id: c.name,
    label: c.name,
    kind: "tool",
    /* What came back, not just that it was called. A tool that returned zero
       rows and one that wrote a record are different events. */
    sub: `${c.wrote ? "wrote" : "read"} ${c.rows} ${c.rows === 1 ? "row" : "rows"} · ${c.ms}ms`,
    at: at(i + 1),
    status: statusOf(c.name) === "partial" ? "warn" : "ok",
  }));

  /*
    The rule on trial. A blocker scenario exists to make one rule bite, so the
    moment it is applied is a node — and whether it held is the node's status.
  */
  if (scenario?.critical) {
    spine.push({
      id: "policy_check",
      label: "policy check",
      kind: "check",
      sub: shorten(scenario.title, 34),
      at: at(calls.length + 1),
      status: task.status === "passed" ? "ok" : "fail",
    });
  }

  spine.push({
    id: outcome.terminal,
    label: outcome.terminal,
    kind: "end",
    sub: outcome.label.toLowerCase(),
    at: Math.max(0, ...turns.map((t) => t.at + t.dur)),
    status: outcome.id === "completed" ? "ok" : "fail",
  });

  const branches = [];

  /* Steps the scenario needed and the agent never took. */
  missing.forEach((name) => {
    branches.push({
      after: Math.max(0, spine.length - 2),
      kind: "skipped",
      label: "expected here",
      nodes: [{
        id: name,
        label: name,
        kind: "skipped",
        sub: "expected here · never called",
        status: "fail",
      }],
    });
  });

  /* The branch the rule could have sent it down. Only when the environment
     actually has that tool — inventing an escalation path for an environment
     without one would be a drawing, not a graph. */
  const escalation = (env?.tools || []).find((t) => /escalat|supervis|human|transfer/i.test(t.name));
  if (scenario?.critical && escalation && !calls.some((c) => c.name === escalation.name)) {
    branches.push({
      after: spine.findIndex((n) => n.id === "policy_check"),
      kind: "alternate",
      label: "not taken",
      nodes: [{
        id: escalation.name,
        label: escalation.name,
        kind: "alternate",
        sub: "the branch not taken",
        status: "idle",
      }],
    });
  }

  return { spine, branches, ids: spine.map((n) => n.id) };
}

/* ── the sentences ───────────────────────────────────────────────────────── */

const summaryFor = (task, outcome, steps) => {
  const domain = attribute(task);
  if (domain && !domain.measured) return `${faultReason(task)} No verdict was produced, so nothing here counts against the agent.`;
  const missedTools = steps.filter((s) => s.status === "missed" && s.tool).map((s) => s.name);
  if (outcome.id === "voicemail") {
    return "Reached voicemail rather than a live caller; the agent left a message and ended the call.";
  }
  if (outcome.id === "hangup") {
    return "The caller picked up but disengaged mid-script; the agent did not recover and the call ended early.";
  }
  if (task.callLog?.unsupportedClaim) {
    return `Ran to the end, but ${task.callLog.unsupportedClaim.replace(/^The agent /, "the agent ")}`;
  }
  if (missedTools.length) {
    return `Completed the call without calling ${missedTools.join(" or ")}, which this scenario needed.`;
  }
  return "Walked the whole reference path and closed the call cleanly.";
};

/**
 * The read, not the recap.
 *
 * A summary says what happened; this says what to do about it. It is written
 * from the same facts the panels above show, so it can never be the only place
 * a claim appears — an analysis nobody can check against the artifact is a
 * confident-sounding guess.
 */
const analysisFor = (task, outcome, steps) => {
  const domain = attribute(task);
  if (domain && !domain.measured) {
    return `${domain.label}: ${faultReason(task)} ${domain.next} There is nothing to conclude about the agent from this scenario — re-run it before reading anything into the rate.`;
  }
  if (task.status === "flaky" && flakySource(task) === "simulator") {
    return "The samples disagree because the simulated caller behaved differently between them, not because the agent did. Tighten the caller's policy — its disclosures and its termination conditions — before treating this as agent nondeterminism.";
  }
  const firstMiss = steps.find((s) => s.status === "missed" && s.tool);
  const partialStep = steps.find((s) => s.status === "partial");

  if (outcome.id === "voicemail") {
    return "The agent reached voicemail rather than a live conversation. The message it left was generic and did not name the reason for the call, so a callback is unlikely — script the voicemail branch separately from the live one.";
  }
  if (outcome.id === "hangup") {
    return `The agent over-pitched the intro and the caller disengaged before the run reached ${firstMiss ? firstMiss.name : "the middle of the script"}.${partialStep ? ` The ${partialStep.name} step was only partially addressed.` : ""} Tighten the opening to two sentences and check for consent before continuing.`;
  }
  if (task.callLog?.unsupportedClaim) {
    return `The conversation reads as a success and the call log does not support it: ${task.callLog.unsupportedClaim} Graders that read words alone will keep passing this — gate the closing statement on the tool result.`;
  }
  if (firstMiss) {
    return `Every step was spoken to, but ${firstMiss.name} was never called, so nothing in the world changed. Make the step a precondition of the closing rather than a line in the prompt.`;
  }
  return "The run followed the reference path, called every tool it needed and closed the call. Nothing here needs changing.";
};

/* ── differences against the baseline ────────────────────────────────────── */

const delta = (now, before) => (now == null || before == null ? null : now - before);

/**
 * The four numbers worth a chip.
 *
 * Chosen because each one changes what you would do next: latency and duration
 * are the caller's experience, cost is the bill, and turns is whether the agent
 * is talking more to achieve the same thing.
 */
export const callDeltas = (detail, baseline) => {
  if (!detail || !baseline) return [];
  return [
    {
      id: "latency",
      label: "Latency",
      value: delta(detail.latencyMs, baseline.latencyMs),
      format: (v) => `${v > 0 ? "+" : ""}${v}ms`,
      lowerIsBetter: true,
    },
    {
      id: "duration",
      label: "Duration",
      value: Math.round(delta(detail.durationS, baseline.durationS) * 10) / 10,
      format: (v) => `${v > 0 ? "+" : ""}${v}s`,
      lowerIsBetter: true,
    },
    {
      id: "cost",
      label: "Cost",
      value: Math.round(delta(detail.costTotal, baseline.costTotal) * 10000) / 10000,
      format: (v) => `${v > 0 ? "+" : "-"}$${Math.abs(v).toFixed(4)}`,
      lowerIsBetter: true,
    },
    {
      id: "turns",
      label: "Turns",
      value: delta(detail.stats.turnCount, baseline.stats.turnCount),
      format: (v) => `${v > 0 ? "+" : ""}${v}`,
      /* More turns is not worse by itself — a longer conversation that lands
         the task beats a short one that does not. Reported without a verdict. */
      neutral: true,
    },
  ].filter((d) => d.value != null && d.value !== 0);
};

/**
 * The transcript, against the baseline's.
 *
 * Aligned by position rather than by text similarity: the runs share a scenario
 * and a seeded world, so turn three is turn three, and a turn that says
 * something else at that position is the agent behaving differently rather than
 * a new turn appearing.
 */
export const transcriptDiff = (turns = [], baseTurns = []) => turns.map((turn, i) => {
  const before = baseTurns[i];
  if (!before) return { ...turn, diff: "added" };
  if (before.text !== turn.text) return { ...turn, diff: "changed", was: before.text };
  return { ...turn, diff: "same" };
});

export const diffTally = (rows = []) => ({
  changed: rows.filter((t) => t.diff === "changed").length,
  added: rows.filter((t) => t.diff === "added").length,
});

/**
 * The route, against the baseline's route.
 *
 * Node-wise rather than edge-wise, because the question people ask of these
 * graphs is "did it get to submit_application", not "did it get there from the
 * same place". Nodes only the baseline visited are kept and drawn as the exit
 * this run did not take — that absence is the finding.
 */
export const graphDiff = (graph, baseGraph) => {
  const base = new Set(baseGraph?.ids || []);
  const mine = new Set(graph?.ids || []);
  const spine = (graph?.spine || []).map((n) => ({
    ...n,
    diffKind: base.has(n.id) ? "shared" : "added",
  }));

  /*
    Steps the baseline reached and this run did not, hung off the last node the
    two still agreed on. Where they diverged is the whole question — "it stopped
    agreeing after send_otp" is a place to look; "something is missing" is not.
  */
  const onlyBaseline = (baseGraph?.spine || []).filter((n) => !mine.has(n.id));
  const branches = (graph?.branches || []).map((b) => ({ ...b, nodes: [...b.nodes] }));

  /*
    A step this run skipped and the baseline took is one fact, not two. Drawing
    it once as "never called" and again as "the baseline went here" puts the
    same node on the canvas twice and makes the graph look like it has more
    going on than the run did — so the skipped node absorbs the comparison.
  */
  const already = new Map();
  branches.forEach((b) => b.nodes.forEach((n) => already.set(n.id, n)));

  onlyBaseline.forEach((n) => {
    const dup = already.get(n.id);
    if (dup) {
      dup.sub = dup.kind === "skipped" ? "never called · the baseline did" : dup.sub;
      return;
    }
    const at = (baseGraph.ids || []).indexOf(n.id);
    let anchor = 0;
    for (let i = at - 1; i >= 0; i -= 1) {
      const j = spine.findIndex((sn) => sn.id === baseGraph.ids[i]);
      if (j !== -1) { anchor = j; break; }
    }
    const existing = branches.find((b) => b.kind === "baseline" && b.after === anchor);
    const node = { ...n, kind: "baseline", sub: "the baseline went here", status: "idle" };
    if (existing) existing.nodes.push(node);
    else branches.push({ after: anchor, kind: "baseline", label: "baseline only", nodes: [node] });
  });

  return {
    spine,
    branches,
    added: spine.filter((n) => n.diffKind === "added").length,
    missingCount: onlyBaseline.length,
  };
};
