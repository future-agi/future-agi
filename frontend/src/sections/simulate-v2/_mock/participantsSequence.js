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
  callLabelOf,
  fullAnswerOf,
  handoffLabelOf,
  handoffMessageOf,
  shortLineOf,
  tokensOf,
  toolDurationOf,
} from "./participants";
import { USER_NODE } from "./participantsGraph";

const SAME_TIME_S = 1.5;
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
        label: shortLineOf(t.request),
        full: t.request,
        spanId: `t${t.n}`,
        alone: !ws.length && !own.length,
        answer: t.reply
          ? {
              kind: "answer",
              at: turnEnd ?? start,
              label: shortLineOf(t.reply),
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
        label: shortLineOf(t.reply),
        full: t.reply,
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
                label: answerLabelOf(w),
                full: fullAnswerOf(w),
              };
      push({
        kind: "handoff",
        from: parent,
        to: node,
        at: w.delegatedAt,
        label: handoffLabelOf(w),
        full: handoffMessageOf(w) || w.task || null,
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
          result: callLabelOf(c),
          input: c.input ?? null,
          output: c.output ?? null,
          bad: !!(c.blocked || c.failed),
          refused: !!c.blocked,
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
        result: callLabelOf(c),
        input: c.input ?? null,
        output: c.output ?? null,
        bad: !!(c.blocked || c.failed),
        refused: !!c.blocked,
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
      prev.full = prev.turnTexts
        .map((x, i) => `Turn ${i + 1}: ${x}`)
        .join("\n");
      prev.answer = {
        ...s.answer,
        label: `${laneInfo[coord.id].name} answered each one`,
        full: `${laneInfo[coord.id].name} answered all ${prev.turns} turns on its own and didn't hand off.\n\nLast reply: ${s.answer.full}`,
      };
      return;
    }
    steps.push({ ...s, turnTexts: s.kind === "message" ? [s.full] : [] });
  });
  steps.forEach((s, i) => {
    s.n = i + 1;
  });

  /* An answer can't come back before the work it waited on: a hand-off's
     answer sits after its own tool calls and sub-hand-offs (times in the
     record are rounded to a tenth of a second). */
  [...steps].reverse().forEach((s) => {
    if (s.kind !== "handoff" || s.answer?.at == null) return;
    const kids = steps.filter((x) => x.n > s.n && x.from === s.to);
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
          ? `${s.tool} ${s.refused ? "was refused" : "failed"} at step ${s.n}`
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

  return {
    lanes,
    steps,
    chain,
    rows,
    groups,
    problems,
    nameOf,
    end: last,
    notRun: p.observed ? p.notRun || [] : [],
    notObserved: p.notObserved || [],
  };
};
