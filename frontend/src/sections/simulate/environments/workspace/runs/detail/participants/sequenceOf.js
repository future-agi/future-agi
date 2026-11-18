import { SEQUENCE_COPY as C } from "./sequence.constants";
import { readable } from "./sequenceLayout";

/*
  A call's participants as a sequence: one lane per participant (caller,
  coordinator, workers), every step numbered in the order it started, every
  answer drawn coming back with the step it answers. Arrows carry the exact
  recorded words; nothing is summarised or invented.
*/
export const USER_LANE = "user";
export const UNKNOWN_LANE = "__unknown__";

const SAME_TIME_S = 1.5;

const oneLine = (text) =>
  String(text ?? "")
    .replace(/\s*\n+\s*/g, " · ")
    .trim();

const refusalWords = (call) =>
  call.refused ? C.refusalWords[call.reason] ?? null : null;

const outputText = (call) => {
  if (call.refused) return C.refused(refusalWords(call));
  if (call.outputOmitted) return C.notKept;
  const output = call.output;
  if (output == null) return C.noResponse;
  if (typeof output !== "object") return readable(output);
  return Object.entries(output)
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`)
    .join(" · ");
};

const toolStep = (call, from, turn) => ({
  kind: "tool",
  from,
  untied: from === UNKNOWN_LANE,
  tool: call.name,
  at: call.at,
  label: call.name,
  result: outputText(call),
  turn,
  input: call.input ?? null,
  output: call.output ?? null,
  inputOmitted: call.inputOmitted,
  outputOmitted: call.outputOmitted,
  bad: !!call.failed && !call.refused,
  refused: !!call.refused,
  reason: refusalWords(call),
  key: call.key,
  spanId: call.spanId ?? null,
  traceId: call.traceId ?? null,
  durMs: call.durationMs,
  parentKey: call.parentKey ?? null,
  answer: null,
});

const answerOf = (w, start, turnEnd) => {
  const took = w.transfer || w.state === "transferred";
  if (took)
    return {
      kind: "took",
      at: (w.delegatedAt ?? start) + 0.01,
      label: C.tookOver,
      full: C.tookOverFull(w.name),
    };
  // No answer in the record. `at` only places the row; without an end time there is no wait to show.
  const missing = {
    kind: "none",
    at: w.endedAt ?? (turnEnd != null ? turnEnd - 0.05 : null),
    timed: w.endedAt != null,
  };
  if (w.state === "stopped")
    return {
      ...missing,
      bad: false,
      stopped: true,
      label: C.stoppedByUser,
      full: C.stoppedByUserFull,
    };
  if (w.state === "cut_off")
    return {
      ...missing,
      bad: false,
      cut: true,
      label: C.recordCut,
      full: C.recordCutFull,
    };
  if (w.state === "timeout")
    return {
      ...missing,
      bad: true,
      label: C.noAnswer,
      full: w.result || C.noAnswerFull,
    };
  // An end the record names that this view doesn't know: claim no outcome, only a result it holds.
  if (w.state === "unknown" && w.result == null) return null;
  return {
    kind: "answer",
    bad: w.state === "failed",
    at: w.endedAt,
    label: oneLine(w.result),
    full: w.result,
    fullShown: w.resultShown,
  };
};

const stepsOfTurn = (t, coord, laneInfo, push) => {
  const start = t.at ?? 0;
  const turnEnd = t.replyAt ?? null;
  const alone =
    !(t.workers || []).length &&
    !(t.own || []).length &&
    !(t.untied || []).length;
  if (t.request) {
    push({
      kind: "message",
      from: USER_LANE,
      to: coord.id,
      at: t.at,
      label: oneLine(t.request),
      full: t.request,
      fullShown: t.requestShown,
      turn: t.n,
      key: `turn-${t.n}`,
      alone,
      answer: t.reply
        ? {
            kind: "answer",
            // Placed at the request when the reply has no time; then it has no duration either.
            at: turnEnd ?? start,
            timed: turnEnd != null,
            label: oneLine(t.reply),
            full: t.reply,
            fullShown: t.replyShown,
          }
        : {
            kind: "none",
            bad: true,
            at: null,
            label: C.noReply,
            full: C.callEndedBeforeReply(coord.name),
          },
    });
  } else if (t.reply) {
    // The coordinator follows up without the caller asking.
    push({
      kind: "message",
      from: coord.id,
      to: USER_LANE,
      at: t.replyAt,
      label: oneLine(t.reply),
      full: t.reply,
      turn: t.n,
      key: `turn-${t.n}`,
      answer: null,
    });
  }
  (t.workers || []).forEach((w) => {
    laneInfo[w.id] = laneInfo[w.id] || {
      name: w.name,
      role: w.undeclared ? C.workerNotDeclared : C.worker,
    };
    let from = w.parentId || coord.id;
    if (w.unknownParent) {
      laneInfo[UNKNOWN_LANE] = { name: C.unknownLane, role: C.unknownRole };
      from = UNKNOWN_LANE;
    }
    const answer = answerOf(w, start, turnEnd);
    push({
      kind: "handoff",
      from,
      to: w.id,
      at: w.delegatedAt,
      label: oneLine(w.task) || C.noTaskText,
      full: w.task || null,
      fullShown: w.taskShown,
      turn: t.n,
      key: w.key,
      parentKey: w.parentKey ?? null,
      usage: w.usage ?? null,
      spanId: w.spanId,
      traceId: w.traceId,
      endState: w.endState,
      group: from,
      bad: w.state === "timeout" || w.state === "failed",
      answer,
    });
    (w.calls || []).forEach((call) => push(toolStep(call, w.id, t.n)));
  });
  if ((t.untied || []).length)
    laneInfo[UNKNOWN_LANE] = { name: C.unknownLane, role: C.unknownRole };
  (t.untied || []).forEach((call) => push(toolStep(call, UNKNOWN_LANE, t.n)));
  (t.own || []).forEach((call) => {
    if (call.by && !laneInfo[call.by])
      laneInfo[call.by] = {
        name: call.byName,
        role: call.byUndeclared ? C.workerNotDeclared : C.worker,
      };
    push(toolStep(call, call.by || coord.id, t.n));
  });
};

// Turns the coordinator answered alone, back to back, become one step (×N).
const foldQuietTurns = (sorted) => {
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
      prev.replyTexts.push(s.answer.full);
      prev.full = prev.turnTexts
        .map((x, i) => C.turnLine(prev.turnNums[i], x))
        .join("\n");
      // Several turns are one text of ours, not one the API formatted.
      prev.fullShown = null;
      prev.answer = {
        ...s.answer,
        label: oneLine(s.answer.full),
        fullShown: null,
        full: prev.replyTexts
          .map((x, i) => C.turnLine(prev.turnNums[i], x))
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
  return steps;
};

const settleAnswers = (steps, coordId) => {
  // Each step sits under the step the record files it under (its `parent` key). A hand-off or the coordinator's
  // own call at the top of a turn sits under that turn's message; a worker's call filed under nothing, under nothing.
  const byKey = new Map(
    steps.filter((s) => s.kind !== "message").map((s) => [s.key, s]),
  );
  const turnMessage = (turn) =>
    steps.find(
      (x) =>
        x.kind === "message" &&
        x.from === USER_LANE &&
        (x.turn === turn || x.turnNums?.includes(turn)),
    );
  steps.forEach((s) => {
    if (s.kind === "message") s.parent = null;
    else if (s.parentKey != null) s.parent = byKey.get(s.parentKey)?.n ?? null;
    else if (s.kind === "handoff" || s.from === coordId)
      s.parent = turnMessage(s.turn)?.n ?? null;
    else s.parent = null;
  });
  // Durations are the record's own times; the answer row is only placed below its own children.
  steps.forEach((s) => {
    s.endAt = s.answer?.at ?? null;
  });
  [...steps].reverse().forEach((s) => {
    if (s.kind !== "handoff" || s.answer?.at == null) return;
    const kids = steps.filter((x) => x.parent === s.n);
    s.answer = {
      ...s.answer,
      at: Math.max(
        s.answer.at,
        ...kids.map((x) => Math.max(x.at ?? 0, x.answer?.at ?? 0)),
      ),
    };
  });
  steps.forEach((s) => {
    if (s.kind === "tool") s.dur = s.durMs != null ? s.durMs / 1000 : null;
    else if (
      s.at != null &&
      s.endAt != null &&
      s.answer.kind !== "took" &&
      s.answer.timed !== false
    )
      s.dur = Math.max(0, s.endAt - s.at);
    else s.dur = null;
    delete s.endAt;
    s.tools = steps.filter((x) => x.kind === "tool" && x.parent === s.n).length;
  });
};

const groupsOf = (steps) => {
  const groups = [];
  steps.forEach((s, i) => {
    const prev = steps[i - 1];
    const together =
      s.kind === "handoff" &&
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
  return groups;
};

const rowsOf = (steps) => {
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
  // A row with no time keeps its place after the timed rows before it.
  let carry = 0;
  rows.forEach((r, i) => {
    carry = r.at != null ? r.at : carry + 1e-6;
    r._k = r.at != null ? r.at : carry;
    r._i = i;
    r._back = r.type === "go" || r.type === "tool" ? 0 : 1;
  });
  rows.sort(
    (a, b) =>
      a._k - b._k || a._back - b._back || (a._back ? b.n - a.n : a._i - b._i),
  );
  return { rows, last };
};

const loopsOf = (steps) => {
  const keyOf = (s) =>
    s.kind === "tool" ? `t:${s.from}:${s.tool}` : `${s.kind}:${s.from}:${s.to}`;
  const loops = [];
  for (let i = 0; i < steps.length; ) {
    let best = null;
    for (let per = 1; per <= 3; per += 1) {
      const block = steps.slice(i, i + per);
      if (block.length < per || block.some((x) => x.kind === "message")) break;
      if (block.some((x) => x.turn !== block[0].turn)) break;
      const sig = block.map(keyOf).join("|");
      let times = 1;
      while (
        steps
          .slice(i + times * per, i + (times + 1) * per)
          .map(keyOf)
          .join("|") === sig &&
        steps[i + times * per]?.turn === block[0].turn
      )
        times += 1;
      if (times >= 3 && (!best || times * per > best.times * best.per))
        best = { per, times };
    }
    if (!best) {
      i += 1;
      continue;
    }
    const span = steps.slice(i, i + best.per * best.times);
    const id = `loop-${span[0].n}`;
    loops.push({
      id,
      first: span[0].n,
      last: span[span.length - 1].n,
      per: best.per,
      times: best.times,
      turn: span[0].turn,
      steps: span.map((x) => x.n),
      bad: span.some((x) => x.bad || x.answer?.bad),
      badCount: span.filter((x) => x.bad || x.answer?.bad).length,
      outcomes: {
        failed: span.filter(
          (x) =>
            (x.kind === "tool" && x.bad && !x.refused) ||
            (x.kind !== "tool" && x.bad && x.answer?.kind !== "none"),
        ).length,
        refused: span.filter((x) => x.kind === "tool" && x.refused).length,
        noAnswer: span.filter((x) => x.answer?.kind === "none" && x.answer?.bad)
          .length,
      },
      // The round's other steps by worker or tool, never by their full messages.
      with: span
        .slice(1, best.per)
        .map((x) => (x.kind === "tool" ? x.tool : x.to)),
    });
    span.forEach((x) => {
      x.loop = id;
    });
    i += best.per * best.times;
  }
  return loops;
};

const problemsOf = (steps, loops, nameOf) => {
  const problems = steps
    .filter((s) => s.bad || s.answer?.bad)
    .map((s) => ({
      n: s.n,
      text:
        s.kind === "tool"
          ? C.toolFailed(s.tool, s.n)
          : s.kind === "message"
            ? C.noReplyTo(s.n)
            : s.answer?.kind === "none"
              ? C.didNotAnswer(nameOf(s.to), s.n)
              : C.failedAt(nameOf(s.to), s.n),
    }));
  const byN = Object.fromEntries(steps.map((s) => [s.n, s]));
  const chain = new Set();
  problems.forEach(({ n }) => {
    let cur = byN[n];
    while (cur && !chain.has(cur.n)) {
      chain.add(cur.n);
      cur = cur.parent != null ? byN[cur.parent] : null;
    }
  });
  const loopOf = Object.fromEntries(loops.map((l) => [l.id, l]));
  const baseOf = (t) =>
    t.replace(/ at step \d+$/, "").replace(/ step \d+$/, "");
  const merged = [];
  problems.forEach((pr) => {
    const loop = byN[pr.n]?.loop ? loopOf[byN[pr.n].loop] : null;
    if (!loop) {
      merged.push(pr);
      return;
    }
    const group = `${loop.id}|${baseOf(pr.text)}`;
    if (merged.some((m) => m.group === group)) return;
    const same = problems
      .filter(
        (q) => byN[q.n]?.loop === loop.id && baseOf(q.text) === baseOf(pr.text),
      )
      .map((q) => q.n);
    merged.push({
      n: pr.n,
      loop: loop.id,
      group,
      range: same.length > 1 ? [same[0], same[same.length - 1]] : null,
      text: same.length > 1 ? C.times(baseOf(pr.text), same.length) : pr.text,
    });
  });
  // Calls the world refused, apart from problems; repeats in one loop are one line.
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
  return { problems: merged, refusals, chain };
};

const turnGroupsOf = (steps) => {
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
      g.text = s.turnTexts?.[0] || s.full || s.label;
    }
    if (s.bad || s.answer?.bad) g.bad = true;
    if (s.refused) g.refused = (g.refused || 0) + 1;
  });
  turnGroups.forEach((g, i) => {
    g.index = i + 1;
  });
  return turnGroups;
};

/**
 * Participants (from `participantsFromRecord`) → the drawing model:
 * { lanes, steps, rows, groups, loops, turnGroups, problems, refusals, chain,
 *   nameOf, end, notObserved }.
 */
export const sequenceOf = (p, { callerName = null } = {}) => {
  if (!p?.coordinator) return null;
  const coord = {
    ...p.coordinator,
    name: p.coordinator.name || C.unknownCoordinator,
  };
  const laneInfo = {
    [USER_LANE]: { name: callerName || C.caller, role: C.caller },
    [coord.id]: { name: coord.name, role: C.coordinator },
  };
  const raw = [];
  const push = (s) => raw.push({ ...s, _i: raw.length });
  p.turns.forEach((t) => stepsOfTurn(t, coord, laneInfo, push));

  // Start order; a step with no recorded time keeps its API place.
  let carry = -1;
  raw.forEach((s) => {
    carry = s.at != null ? s.at : carry;
    s._k = s.at != null ? s.at : carry;
  });
  const sorted = [...raw].sort((a, b) => a._k - b._k || a._i - b._i);
  const steps = foldQuietTurns(sorted);
  settleAnswers(steps, coord.id);

  const laneOrder = [USER_LANE, coord.id];
  steps.forEach((s) => {
    [s.from, s.to].forEach((id) => {
      if (id && laneInfo[id] && !laneOrder.includes(id)) laneOrder.push(id);
    });
  });
  const lanes = laneOrder.map((id) => ({ id, ...laneInfo[id] }));
  const nameOf = (id) => laneInfo[id]?.name || id;

  const { rows, last } = rowsOf(steps);
  const loops = loopsOf(steps);
  const { problems, refusals, chain } = problemsOf(steps, loops, nameOf);
  steps.forEach((s) => {
    delete s._i;
    delete s._k;
  });

  return {
    lanes,
    steps,
    chain,
    rows,
    groups: groupsOf(steps),
    loops,
    turnGroups: turnGroupsOf(steps),
    problems,
    refusals,
    nameOf,
    end: last,
    notObserved: p.notObserved || [],
  };
};
