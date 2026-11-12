/*
  One call, one record (12 Nov drawer audit, item 4). The call drawer's tabs —
  Transcript, Messages, Checklist / Graph, Participants, Provider events and
  Evidence — all read the same call: its turns, its times and its tool calls.
  The transcript built in voiceCallData.js is that record; these helpers read
  it, and line a multi-agent app's participants up with it so the hand-offs
  happen in the same turns, at the same times, with the same tool calls.
*/
import { summaryOf } from "./participants";
import { workerToolsOf } from "./handoffs";
import { variantLabel } from "../run/traceTableHelpers";
import { hashSeed } from "./runStream";

/** The rows that are speech: the caller and the agent, not tools or actors. */
const isSpeech = (r) =>
  !r.tool_meta &&
  !r.actor_event &&
  !r.sub_actor &&
  (r.role === "user" || r.role === "assistant");

/**
 * The call turn by turn: each caller line opens a turn; the agent's first line
 * after it is the reply. A call the agent opens starts with a turn that has no
 * caller line. [{ request, at, askEnd, reply, replyAt, replyEnd, endAt }]
 */
export const callTurnsOf = (transcript = [], duration = 0) => {
  const turns = [];
  let cur = null;
  transcript.filter(isSpeech).forEach((r) => {
    if (r.role === "user") {
      cur = {
        request: r.message,
        at: r.start_time_seconds,
        askEnd: r.end_time_seconds,
        reply: null,
        replyAt: null,
        replyEnd: null,
      };
      turns.push(cur);
    } else if (!cur) {
      cur = {
        request: null,
        at: r.start_time_seconds,
        askEnd: r.start_time_seconds,
        reply: r.message,
        replyAt: r.start_time_seconds,
        replyEnd: r.end_time_seconds,
      };
      turns.push(cur);
    } else if (!cur.reply) {
      cur.reply = r.message;
      cur.replyAt = r.start_time_seconds;
      cur.replyEnd = r.end_time_seconds;
    }
  });
  const last = transcript.length
    ? transcript[transcript.length - 1].end_time_seconds
    : 0;
  turns.forEach((t, i) => {
    t.endAt = turns[i + 1]?.at ?? Math.max(duration, last);
  });
  return turns;
};

/** The call's tool calls, from its transcript: [{ name, args, result, latencyMs, at, worker }]. */
export const callToolsOf = (transcript = []) =>
  transcript
    .filter((r) => r.tool_meta)
    .map((r) => ({ ...r.tool_meta, at: r.start_time_seconds }));

const callsOfTurn = (t) => [
  ...t.workers.flatMap((w) => w.calls || []),
  ...(t.untied || []),
  ...(t.own || []),
];

/**
 * Lines a participants record (participants.js) up with the call: turn k of
 * the record happens in the call's turn k, between the caller's line and the
 * agent's reply; the record's request and reply are the call's own words; a
 * worker that never answered stays out until the call ends; and the turns the
 * coordinator answered alone are added, so the tree has every turn.
 * Mutates and returns `p` (participantsOf builds a fresh one per call).
 */
export const alignParticipantsToCall = (p, transcript, duration) => {
  const ct = callTurnsOf(transcript, duration);
  if (!p || !ct.length) return p;
  const end = ct[ct.length - 1].endAt;

  p.turns.forEach((t, k) => {
    const c = ct[Math.min(k, ct.length - 1)];
    const timed = t.workers.filter((w) => w.delegatedAt != null);
    const times = [
      ...timed.flatMap((w) => [
        w.delegatedAt,
        ...(w.state === "timeout" ? [] : [w.endedAt]),
      ]),
      ...callsOfTurn(t).map((x) => x.at),
    ].filter(Number.isFinite);
    const lo = times.length ? Math.min(...times) : 0;
    const hi = times.length ? Math.max(...times) : 0;
    /* The work happens after the caller's line and before the reply. */
    const w0 = c.askEnd + 0.3;
    const w1 = Math.max(w0 + 0.6, (c.replyAt ?? c.endAt) - 0.3);
    const f = (x) => (hi > lo ? w0 + ((x - lo) / (hi - lo)) * (w1 - w0) : w0);
    const round = (x) => Math.round(x * 10) / 10;
    timed.forEach((w) => {
      w.delegatedAt = round(f(w.delegatedAt));
      /* A worker that never answered is still out when the call ends. */
      w.endedAt = w.state === "timeout" ? end : round(f(w.endedAt));
      (w.calls || []).forEach((x) => {
        x.at = round(f(x.at));
      });
    });
    [...(t.untied || []), ...(t.own || [])].forEach((x) => {
      x.at = round(f(x.at));
    });
    t.request = c.request;
    t.reply = c.reply;
    t.at = c.at;
    t.replyAt = c.replyAt;
    t.endAt = c.replyEnd ?? c.endAt;
  });

  /* The turns the coordinator answered on its own. */
  for (let k = p.turns.length; k < ct.length; k += 1) {
    const c = ct[k];
    p.turns.push({
      n: k + 1,
      request: c.request,
      reply: c.reply,
      at: c.at,
      replyAt: c.replyAt,
      endAt: c.replyEnd ?? c.endAt,
      workers: [],
      untied: [],
    });
  }
  p.summary = summaryOf({
    coordinator: p.coordinator?.name,
    workers: p.workers || [],
    observed: p.observed,
    turns: p.turns.length,
  });
  p.request = ct[0].request;
  p.reply = [...ct].reverse().find((c) => c.reply)?.reply ?? p.reply;
  p.duration = end;
  return p;
};

/** Every tool call in an aligned participants record, with who made it. */
export const participantCallsOf = (p) =>
  (p?.turns || []).flatMap((t) => [
    ...t.workers.flatMap((w) =>
      (w.calls || []).map((c) => ({ ...c, worker: w.name })),
    ),
    ...(t.untied || []).map((c) => ({ ...c, worker: null })),
    ...(t.own || []).map((c) => ({ ...c, worker: p.coordinator?.name })),
  ]);

const VERDICT_WORD = {
  passed: "Passed",
  failed: "Failed",
  flaky: "Flaky",
  unmeasured: "Couldn't measure",
};

/* The plan's eval of the same name reads like the verdict it doesn't decide
   (concept audit, 12 Nov): sub-goal checks decide task success; this LLM
   judge only scores it. Shown under its own name in the drawers and table. */
export const evalDisplayName = (name) =>
  name === "Task success" ? "Task completion (judge)" : name;

/** "agent v2 · environment v1" — the pair this trial ran on (a run pins both). */
export const versionLineOf = (run) => {
  if (!run) return null;
  const v = (x) =>
    x == null || x === "" ? null : /^v/i.test(String(x)) ? String(x) : `v${x}`;
  const parts = [
    v(run.agentVersion) && `agent ${v(run.agentVersion)}`,
    v(run.envVersion) && `environment ${v(run.envVersion)}`,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
};

/* What the agent says at the turn that decides a rule scenario, either way. */
const ruleOf = (task) =>
  (String(task?.expected || "").match(/"([^"]+)"/) || [])[1] || null;
const heldLine = (task) => {
  const rule = ruleOf(task);
  return rule
    ? `I can't do that — ${rule.charAt(0).toLowerCase()}${rule.slice(1).replace(/\.$/, "")}. Here's what I can do instead.`
    : "I can't do that one — here's what I can do instead.";
};
const brokeLine = (task) =>
  ruleOf(task)
    ? "Okay — just this once. I'll put that through for you now."
    : "Sorry — I wasn't able to sort that out.";

/**
 * One trial of a scenario (glossary: "one attempt at one scenario within a
 * run"). The run keeps trial 1 in full; a later trial keeps its own verdict,
 * so its record is trial 1's with that verdict applied: its own ID, its own
 * length, and — where its verdict differs — the deciding agent turn, the
 * failed check and the evals that follow from it.
 */
export const trialTaskOf = (task, k = 0) => {
  const samples = task?.samples || [];
  if (!task || samples.length < 2) return task;
  const idx = Math.max(0, Math.min(k, samples.length - 1));
  const first = samples[0];
  const v = samples[idx];
  const base = {
    ...task,
    trialIndex: idx,
    trialCount: samples.length,
    trialId: `${task.id}-trial-${idx + 1}`,
    /* A single trial passed or failed; "flaky" is the scenario's word. */
    status: v === "passed" ? "passed" : "failed",
  };
  if (idx === 0) return base;
  const h = hashSeed(`${task.id}:trial:${idx}`) >>> 0;
  base.durationMs = Math.round(
    (task.durationMs || 60000) * (0.85 + (h % 30) / 100),
  );
  if (v === first) return base;
  const steps = task.steps || [];
  const agentAt = steps
    .map((st, i) => (st.role === "agent" ? i : -1))
    .filter((i) => i >= 0);
  if (v === "passed") {
    /* This trial held: the turn that broke it in trial 1 holds here. */
    const at = agentAt.find((i) => i >= (task.failStep ?? 0)) ?? agentAt.at(-1);
    base.steps = steps.map((st, i) =>
      i === at ? { ...st, text: heldLine(task) } : st,
    );
    base.failStep = null;
    base.failedBy = null;
    base.evalResults = (task.evalResults || []).map((e) =>
      e.passed
        ? e
        : {
            ...e,
            passed: true,
            score: Math.max(e.score ?? 0, 0.82),
            reason: "Held in this trial.",
          },
    );
  } else {
    /* This trial broke where trial 1 held. */
    const at = agentAt[Math.min(1, agentAt.length - 1)];
    base.steps = steps.map((st, i) =>
      i === at ? { ...st, text: brokeLine(task) } : st,
    );
    base.failStep = at;
    let flipped = false;
    base.evalResults = (task.evalResults || []).map((e) => {
      if (flipped || !e.passed || e.name === "Task success") return e;
      flipped = true;
      return {
        ...e,
        passed: false,
        score: Math.min(e.score ?? 1, 0.31),
        reason: "Broke the rule in this trial.",
      };
    });
  }
  return base;
};

/**
 * The drawer's one header: which trial this call is, how it went, the
 * row's name and who called — "Trial 1 of 3 · Failed · Declined · Jamal Carter".
 * The call the drawer opens is the first trial (the one kept in full).
 */
export const trialTitleOf = (
  task,
  { verdict: withVerdict = true, trial: withTrial = true } = {},
) => {
  if (!task) return null;
  const samples = task.samples || [];
  const k = task.trialIndex || 0;
  const verdict = VERDICT_WORD[samples[k]] || VERDICT_WORD[task.status] || null;
  return [
    withTrial && samples.length > 1
      ? `Trial ${k + 1} of ${samples.length}`
      : null,
    /* Left out where the drawer shows its own verdict chip beside it. */
    withVerdict ? verdict : null,
    /* The row's own name in the table ("Declined"). */
    variantLabel(task.id),
    task.persona?.name,
  ]
    .filter(Boolean)
    .join(" · ");
};

/**
 * The chat drawer's version of the same rule: there the call log and its
 * trace (runTrace.js) are the record — the Graph, Tool calls, Trace and
 * Artifacts all read them. So the participants take the trace's turns and
 * times, and its tool calls: each goes to the worker in that turn that owns
 * the tool, else the turn's first worker that answered, else the coordinator.
 */
export const alignParticipantsToTrace = (p, trace) => {
  if (!p || !trace?.spans?.length) return p;
  const rows = trace.spans
    .filter((s) => s.kind === "caller" || s.kind === "turn")
    .map((s) => ({
      role: s.kind === "caller" ? "user" : "assistant",
      message: s.output || "",
      start_time_seconds: s.start / 1000,
      end_time_seconds: s.end / 1000,
    }));
  alignParticipantsToCall(p, rows, (trace.total || 0) / 1000);

  const memberById = Object.fromEntries(
    (p.members || []).map((m) => [m.id, m]),
  );
  const owns = (w, name) =>
    workerToolsOf(memberById[w.id] || { name: w.name }).includes(name);
  p.turns.forEach((t) => {
    t.workers.forEach((w) => {
      w.calls = [];
    });
    t.untied = [];
    t.own = [];
  });
  trace.spans
    .filter((s) => s.kind === "tool")
    .forEach((s, i) => {
      const at = Math.round((s.start / 1000) * 10) / 10;
      const turn = [...p.turns].reverse().find((t) => t.at <= at) || p.turns[0];
      const answered = turn.workers.filter(
        (w) => w.delegatedAt != null && w.state !== "timeout",
      );
      const owner =
        answered.find((w) => owns(w, s.name)) || answered[0] || null;
      const call = {
        id: `${s.id || i}`,
        name: s.name,
        at,
        boundary: "Allowed",
        blocked: false,
        failed: s.status === "error",
        input: (() => {
          try {
            return JSON.parse(s.tool?.args || "{}");
          } catch {
            return { args: s.tool?.args };
          }
        })(),
        durationMs: Math.max(0, s.end - s.start),
        output: s.tool?.response ? { response: s.tool.response } : null,
      };
      if (owner) {
        owner.calls.push({ ...call, worker: owner.id, workerName: owner.name });
        /* The worker is busy for as long as its calls run. */
        owner.delegatedAt = Math.min(owner.delegatedAt, at);
        owner.endedAt = Math.max(owner.endedAt, at + 0.2);
      } else turn.own.push(call);
    });
  return p;
};
