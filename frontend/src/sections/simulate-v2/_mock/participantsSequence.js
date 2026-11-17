/*
  A call's participants record as a sequence: one lane per participant
  (caller, coordinator, workers), time going down, every step numbered in
  the order it started and labelled with what it asked; every answer drawn
  coming back with what came back and the number of the step it answers.

  Design A of the Participants steps review (16 Nov): the manager's "how
  would the user know what happened in step 1, 2, 3". Tool calls are steps of
  their own on the lane of the agent that ran them; the caller's turns that
  the coordinator answered alone fold into one step with a turn count.

  The words: a short label per step (stored w.label / w.answerLabel when the
  record has one, otherwise derived), the exact message in the step detail.
  Problem wording comes from the recorded state, never generated.
*/
import {
  NO_ANSWER,
  NO_EVENTS,
  NOT_TIED,
  answerLabelOf,
  fullAnswerOf,
  handoffMessageOf,
  resultSentenceOf,
  tokensOf,
  toolDurationOf,
} from "./participants";
import { USER_NODE } from "./participantsGraph";

const SAME_TIME_S = 1.5;

/* Karthik, 17 Nov: the record has the exact text, not a summary. Arrows show
   the exact words (on one line, cut to fit by the diagram) — never a label we
   made up from tool names or outputs. */
const oneLine = (t) =>
  String(t ?? "")
    .replace(/\s*\n+\s*/g, " · ")
    .trim();
/* What the worker itself handed back — its reply only. Its tool calls are
   steps of their own, so their outputs aren't repeated in the answer. */
const replyOf = (w) => (w.recorded ? w.result : resultSentenceOf(w));
/* Karthik, 17 Nov: a call the world refused is its own outcome, with the
   world's reason — not a failure. A call outside the worker's tools is
   refused too (the world stopped it); a broken tool is a failure. */
const isRefused = (c) => !!(c.blocked || c.refused);
const refusalOf = (c) =>
  c.reason ??
  c.output?.reason ??
  (c.blocked ? "Not one of this worker's tools" : null);
/* A tool's output as recorded: text as is, an object as its own fields. */
const outputText = (c) => {
  if (isRefused(c)) return `Refused${refusalOf(c) ? ` · ${refusalOf(c)}` : ""}`;
  const o = c.output;
  if (o == null) return "No response";
  if (typeof o !== "object") return String(o);
  return Object.entries(o)
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`)
    .join(" · ");
};
const UNTIED = "__untied__";

/**
 * The record → { lanes, steps, rows, groups, problems, notRun }.
 * steps: numbered, each { n, kind: message|handoff|tool, from, to, at, label,
 *   full, answer: { kind: answer|none|took, at, label, full, bad } | null,
 *   bad, spanId, turns }.
 * rows: what the diagram draws top to bottom, each { type: go|tool|back|none|
 *   took, n, at }.
 * groups: [first n, last n] runs of hand-offs sent at the same time.
 */
export const sequenceOf = (p, { callerName = null } = {}) => {
  if (!p?.coordinator) return null;
  const coord = p.coordinator;
  const blockNode = Object.fromEntries(
    [...(p.workers || []), ...(p.undeclared || [])].map((w) => [
      w.id,
      w.memberId || w.id,
    ]),
  );
  const laneInfo = {
    [USER_NODE]: { name: callerName || "Caller", role: "Caller" },
    [coord.id]: { name: coord.name, role: "Coordinator" },
  };
  const raw = [];
  let order = 0;
  const push = (s) => raw.push({ ...s, _i: order++ });

  p.turns.forEach((t) => {
    const ws = [
      ...(t.workers || []),
      ...(p.undeclared || []).filter(
        (u) => (u.turn ?? 0) === t.n - 1 && !(t.workers || []).includes(u),
      ),
    ];
    const own = [...(t.untied || []), ...(t.own || [])];
    const start = t.at ?? 0;
    const turnEnd = t.replyAt ?? t.endAt ?? null;
    if (t.request) {
      push({
        kind: "message",
        from: USER_NODE,
        to: coord.id,
        at: start,
        label: oneLine(t.request),
        full: t.request,
        turn: t.n,
        spanId: `t${t.n}`,
        alone: !ws.length && !own.length,
        answer: t.reply
          ? {
              kind: "answer",
              at: turnEnd ?? start,
              label: oneLine(t.reply),
              full: t.reply,
            }
          : {
              kind: "none",
              bad: true,
              at: null,
              label: "No reply",
              full: `The call ended before ${coord.name} replied.`,
            },
      });
    } else if (t.reply) {
      /* The coordinator follows up without the caller asking. */
      push({
        kind: "message",
        from: coord.id,
        to: USER_NODE,
        at: start,
        label: oneLine(t.reply),
        full: t.reply,
        turn: t.n,
        spanId: `t${t.n}`,
        answer: null,
      });
    }
    ws.forEach((w) => {
      const node = blockNode[w.id] || w.id;
      const parent =
        (w.parentId && blockNode[w.parentId]) || w.parentId || coord.id;
      laneInfo[node] = laneInfo[node] || {
        name: w.name,
        role: w.undeclared ? "Worker · not declared" : "Worker",
      };
      const unobserved = w.state === "unobserved";
      const took = w.transfer || w.state === "transferred";
      const timedOut =
        !unobserved && !took && (w.state === "timeout" || w.endedAt == null);
      const failed = w.state === "failed" || !!w.failed;
      const answer = took
        ? {
            kind: "took",
            at: (w.delegatedAt ?? start) + 0.01,
            label: "Took over the call",
            full: `${w.name} took over the call. It doesn't hand back.`,
          }
        : unobserved
          ? { kind: "none", at: null, label: "No events", full: NO_EVENTS }
          : timedOut
            ? {
                kind: "none",
                bad: true,
                /* Drawn when the turn gave up on it, never an invented time. */
                at: w.endedAt ?? (turnEnd != null ? turnEnd - 0.05 : null),
                label: "No answer came back",
                full: fullAnswerOf(w) || NO_ANSWER,
              }
            : {
                kind: "answer",
                bad: failed,
                at: w.endedAt,
                label: oneLine(replyOf(w)) || answerLabelOf(w),
                full: replyOf(w),
              };
      push({
        kind: "handoff",
        from: parent,
        to: node,
        at: w.delegatedAt,
        label:
          oneLine(handoffMessageOf(w) || w.task) ||
          "No task text in the record",
        full: handoffMessageOf(w) || w.task || null,
        turn: t.n,
        spanId: `t${t.n}:${w.id}`,
        model: w.model ?? null,
        tokens: tokensOf(w),
        group: w.parallelWith ? parent : null,
        bad: timedOut || failed,
        answer,
      });
      (w.calls || []).forEach((c) =>
        push({
          kind: "tool",
          from: node,
          tool: c.name,
          at: c.at,
          label: c.name,
          result: outputText(c),
          turn: t.n,
          input: c.input ?? null,
          output: c.output ?? null,
          bad: !!c.failed && !isRefused(c),
          refused: isRefused(c),
          reason: isRefused(c) ? refusalOf(c) : null,
          spanId: `t${t.n}:${c.id}`,
          durMs: c.durationMs ?? toolDurationOf(c),
          answer: null,
        }),
      );
    });
    /* A call the record ties to no worker sits on an Unknown lane — never
       guessed onto the coordinator (plan MA-2: missing attribution is
       Unknown). The coordinator's own calls stay on its lane. */
    const untied = new Set(t.untied || []);
    if (untied.size) laneInfo[UNTIED] = { name: NOT_TIED, role: "Unknown" };
    own.forEach((c) =>
      push({
        kind: "tool",
        from: untied.has(c) ? UNTIED : coord.id,
        untied: untied.has(c),
        tool: c.name,
        at: c.at,
        label: c.name,
        result: outputText(c),
        turn: t.n,
        input: c.input ?? null,
        output: c.output ?? null,
        bad: !!c.failed && !isRefused(c),
        refused: isRefused(c),
        reason: isRefused(c) ? refusalOf(c) : null,
        spanId: `t${t.n}:${c.id}`,
        durMs: c.durationMs ?? toolDurationOf(c),
        answer: null,
      }),
    );
  });

  /* Start order; a step with no recorded time keeps its place. */
  const key = (s) => (s.at != null ? s.at : 1e6 + s._i);
  const sorted = raw.sort((a, b) => key(a) - key(b) || a._i - b._i);

  /* Turns the coordinator answered alone, back to back: one step, ×N. */
  const steps = [];
  sorted.forEach((s) => {
    const prev = steps[steps.length - 1];
    if (
      s.kind === "message" &&
      s.alone &&
      s.answer?.kind === "answer" &&
      prev?.kind === "message" &&
      prev.alone &&
      prev.answer?.kind === "answer"
    ) {
      prev.turns = (prev.turns || 1) + 1;
      prev.turnTexts.push(s.full);
      prev.turnNums.push(s.turn);
      /* The record's own turn numbers, not 1, 2, 3 inside the step. */
      prev.full = prev.turnTexts
        .map((x, i) => `Turn ${prev.turnNums[i] ?? i + 1}: ${x}`)
        .join("\n");
      /* Each reply as recorded, numbered by the record's turns — no
         summary line of ours. The arrow shows the last one. */
      prev.replyTexts.push(s.answer.full);
      prev.answer = {
        ...s.answer,
        label: oneLine(s.answer.full),
        full: prev.replyTexts
          .map((x, i) => `Turn ${prev.turnNums[i] ?? i + 1}: ${x}`)
          .join("\n"),
      };
      return;
    }
    steps.push({
      ...s,
      turnTexts: s.kind === "message" ? [s.full] : [],
      turnNums: s.kind === "message" ? [s.turn] : [],
      replyTexts: s.kind === "message" && s.answer?.full ? [s.answer.full] : [],
    });
  });
  steps.forEach((s, i) => {
    s.n = i + 1;
  });

  /* An answer can't come back before the work it waited on: a hand-off's
     answer sits after its own tool calls and sub-hand-offs (times in the
     record are rounded to a tenth of a second). */
  [...steps].reverse().forEach((s) => {
    if (s.kind !== "handoff" || s.answer?.at == null) return;
    /* Only its own round: the next hand-off to the same worker starts a new
       one (a worker asked again in a loop answers each time). */
    const again = steps.find(
      (x) => x.n > s.n && x.kind === "handoff" && x.to === s.to,
    );
    const kids = steps.filter(
      (x) => x.n > s.n && x.from === s.to && (!again || x.n < again.n),
    );
    const latest = Math.max(
      s.answer.at,
      ...kids.map((x) => Math.max(x.at ?? 0, x.answer?.at ?? 0)),
    );
    s.answer = { ...s.answer, at: latest };
  });

  /* Who each step works for: the hand-off (or the caller's message) that
     gave the work to the agent taking this step. */
  steps.forEach((s, i) => {
    const before = steps.slice(0, i).reverse();
    const parent =
      before.find((x) => x.kind !== "tool" && x.to === s.from) || null;
    s.parent = s.from === USER_NODE ? null : parent?.n ?? null;
  });

  /* How long a step took: until its answer came back (or the turn gave up on
     it), or the tool call's own time. A take-over has no end. */
  steps.forEach((s) => {
    if (s.kind === "tool") s.dur = s.durMs != null ? s.durMs / 1000 : null;
    else if (s.at != null && s.answer?.at != null && s.answer.kind !== "took")
      s.dur = Math.max(0, s.answer.at - s.at);
    else s.dur = null;
    s.tools = steps.filter((x) => x.kind === "tool" && x.parent === s.n).length;
  });

  /* Hand-offs from one parent sent within a moment of each other. */
  const groups = [];
  steps.forEach((s, i) => {
    const prev = steps[i - 1];
    const together =
      s.kind === "handoff" &&
      s.group &&
      prev?.kind === "handoff" &&
      prev.group === s.group &&
      s.at != null &&
      prev.at != null &&
      Math.abs(s.at - prev.at) < SAME_TIME_S;
    if (!together) return;
    const g = groups[groups.length - 1];
    if (g && g[1] === prev.n) g[1] = s.n;
    else groups.push([prev.n, s.n]);
  });

  /* Lanes: caller, coordinator, then workers in the order they first act. */
  const laneOrder = [USER_NODE, coord.id];
  steps.forEach((s) => {
    [s.from, s.to].forEach((id) => {
      if (id && laneInfo[id] && !laneOrder.includes(id)) laneOrder.push(id);
    });
  });
  const lanes = laneOrder.map((id) => ({ id, ...laneInfo[id] }));
  const nameOf = (id) => laneInfo[id]?.name || id;

  /* Rows top to bottom: each step where it starts, each answer where it
     comes back; an answer with no time goes after everything else. */
  const last = Math.max(
    0,
    ...steps.flatMap((s) => [s.at ?? 0, s.answer?.at ?? 0]),
  );
  const rows = [];
  steps.forEach((s) => {
    rows.push({ type: s.kind === "tool" ? "tool" : "go", n: s.n, at: s.at });
    if (s.answer)
      rows.push({
        type:
          s.answer.kind === "answer"
            ? "back"
            : s.answer.kind === "took"
              ? "took"
              : "none",
        n: s.n,
        at: s.answer.at,
      });
  });
  const rowKey = (r, i) => (r.at != null ? r.at : last + 1 + i * 1e-3);
  rows.forEach((r, i) => {
    r._k = rowKey(r, i);
    r._i = i;
    r._back = r.type === "go" || r.type === "tool" ? 0 : 1;
  });
  /* Same moment: steps before answers, and answers last-in first-out — a
     worker answers before the one that handed it the work does. */
  rows.sort(
    (a, b) =>
      a._k - b._k || a._back - b._back || (a._back ? b.n - a.n : a._i - b._i),
  );

  /* What went wrong, in the record's words, with the step it happened at. */
  const problems = steps
    .filter((s) => s.bad || s.answer?.bad)
    .map((s) => ({
      n: s.n,
      text:
        s.kind === "tool"
          ? `${s.tool} failed at step ${s.n}`
          : s.kind === "message"
            ? `No reply to step ${s.n}`
            : s.answer?.kind === "none"
              ? `${nameOf(s.to)} didn't answer step ${s.n}`
              : `${nameOf(s.to)} failed at step ${s.n}`,
    }));

  /* A problem and the steps that led to it, for "Problems only". */
  const byN = Object.fromEntries(steps.map((s) => [s.n, s]));
  const chain = new Set();
  problems.forEach(({ n }) => {
    let cur = byN[n];
    while (cur && !chain.has(cur.n)) {
      chain.add(cur.n);
      cur = cur.parent != null ? byN[cur.parent] : null;
    }
  });

  /* Loops (Chintan, 17 Nov — a 20–30 step call with loops can't be read
     step by step): inside one turn, the same steps repeated 3 or more times
     in a row — one step (a tool retried) or a short cycle (hand off, the
     worker calls a tool, hand off again). Each becomes one row that opens. */
  const keyOf = (s) =>
    s.kind === "tool" ? `t:${s.from}:${s.tool}` : `${s.kind}:${s.from}:${s.to}`;
  const loops = [];
  for (let i = 0; i < steps.length; ) {
    let best = null;
    for (let per = 1; per <= 3; per += 1) {
      const block = steps.slice(i, i + per);
      if (block.length < per || block.some((x) => x.kind === "message")) break;
      if (block.some((x) => x.turn !== block[0].turn)) break;
      let times = 1;
      while (
        steps
          .slice(i + times * per, i + (times + 1) * per)
          .map(keyOf)
          .join("|") === block.map(keyOf).join("|") &&
        steps[i + times * per]?.turn === block[0].turn
      )
        times += 1;
      if (times >= 3 && (!best || times * per > best.times * best.per))
        best = { per, times };
    }
    if (best) {
      const span = steps.slice(i, i + best.per * best.times);
      loops.push({
        id: `loop-${span[0].n}`,
        first: span[0].n,
        last: span[span.length - 1].n,
        per: best.per,
        times: best.times,
        turn: span[0].turn,
        steps: span.map((x) => x.n),
        bad: span.some((x) => x.bad || x.answer?.bad),
        badCount: span.filter((x) => x.bad || x.answer?.bad).length,
        /* Each outcome counted apart: a no-answer isn't a failure. */
        outcomes: {
          failed: span.filter(
            (x) =>
              (x.kind === "tool" && x.bad && !x.refused) ||
              (x.kind !== "tool" && x.bad && x.answer?.kind !== "none"),
          ).length,
          refused: span.filter((x) => x.kind === "tool" && x.refused).length,
          noAnswer: span.filter(
            (x) => x.answer?.kind === "none" && x.answer?.bad,
          ).length,
        },
        /* The other steps of each round, named on the loop's row. */
        with: span
          .slice(1, best.per)
          .map((x) => (x.kind === "tool" ? x.tool : x.label)),
      });
      span.forEach((x) => {
        x.loop = `loop-${span[0].n}`;
      });
      i += best.per * best.times;
    } else i += 1;
  }
  /* The same problem repeated in a loop is one line, with its count. */
  const loopOf = Object.fromEntries(loops.map((l) => [l.id, l]));
  /* Grouped by loop and by what went wrong, in the record's words: a
     refusal, a failure and a no-answer in one loop are three lines. */
  const baseOf = (t) =>
    t.replace(/ at step \d+$/, "").replace(/ step \d+$/, "");
  const merged = [];
  problems.forEach((pr) => {
    const st = byN[pr.n];
    const l = st?.loop ? loopOf[st.loop] : null;
    if (!l) return merged.push(pr);
    const group = `${l.id}|${baseOf(pr.text)}`;
    if (merged.some((m) => m.group === group)) return undefined;
    const same = problems
      .filter(
        (q) => byN[q.n]?.loop === l.id && baseOf(q.text) === baseOf(pr.text),
      )
      .map((q) => q.n);
    return merged.push({
      n: pr.n,
      loop: l.id,
      group,
      range: same.length > 1 ? [same[0], same[same.length - 1]] : null,
      text:
        same.length > 1 ? `${baseOf(pr.text)} ${same.length} times` : pr.text,
    });
  });
  /* Calls the world refused, each with its reason — counted apart from
     problems. A refusal can be the world doing its job; whether trying was
     wrong is for a sub-goal check to say. Repeats in one loop are one line. */
  const refusals = [];
  steps
    .filter((s) => s.refused)
    .forEach((s) => {
      const group = `${s.loop || s.n}|${s.tool}|${s.reason}`;
      const had = refusals.find((r) => r.group === group);
      if (had) {
        had.range = [had.n, s.n];
        had.times += 1;
      } else
        refusals.push({
          n: s.n,
          group,
          tool: s.tool,
          reason: s.reason,
          range: null,
          times: 1,
        });
    });
  /* The caller's turns, for grouping a long call (each step keeps its turn). */
  const turnGroups = [];
  steps.forEach((s) => {
    let g = turnGroups.find((x) => x.turn === s.turn);
    if (!g) {
      g = { turn: s.turn, steps: [], label: null, bad: false };
      turnGroups.push(g);
    }
    g.steps.push(s.n);
    if (!g.label && s.kind === "message") {
      g.label = s.label;
      /* The header quotes what was said, not just its first sentence. */
      g.text = s.turnTexts?.[0] || s.full || s.label;
    }
    if (s.bad || s.answer?.bad) g.bad = true;
    if (s.refused) g.refused = (g.refused || 0) + 1;
  });
  turnGroups.forEach((g, i) => {
    g.index = i + 1;
  });

  return {
    lanes,
    steps,
    chain,
    rows,
    groups,
    loops,
    turnGroups,
    problems: merged,
    refusals,
    nameOf,
    end: last,
    notRun: p.observed ? p.notRun || [] : [],
    notObserved: p.notObserved || [],
  };
};
