// Side by side · the story grid (pure; the Why line and names use it). Each lane is a numbered
// story (1, 2, 3 … in that run's own order); a row of the grid is one aligned
// step, so matching steps sit side by side. Steps only one run took fold into
// `+ 9 more steps only in the coding harness Code` (never when they hold the first wrong
// step, a failed check or a step that went badly). Also the 10-second
// summary: who passed (`Support Agent v3 (baseline) failed all 3 tries; …`)
// and why (`Support Agent v3 dropped "A-" from the order id in its first
// step, so its "Order found" check failed.`). No React; tested in
// __tests__/storyGrid.test.js.
import { isChatter, milestoneBeat, thinkBeat } from "./storyModel";
import { orderIdOf } from "./whyModel";

/** `the coding harness Code × claude-sonnet-4-5` → `the coding harness Code`; `A · b` → `A`. */
export const shortName = (name) =>
  String(name ?? "")
    .split(/\s+[×·]\s+/)[0]
    .trim();

/**
 * Human names for lanes: short names, falling back to the full name (then
 * `name (#2B)`) when two lanes would read the same. Blind names win.
 * @returns {Map<string, string>} lane id → name
 */
export function laneNames(lanes = []) {
  const full = (l) =>
    l.blindName ??
    (l.kind === "production" ? "Production" : l.name || l.label || "Run");
  const tries = [
    (l) => (l.blindName ? full(l) : shortName(full(l)) || full(l)),
    full,
    (l) => (l.blindName ? full(l) : `${full(l)} (${l.label})`),
  ];
  for (const pick of tries) {
    const names = lanes.map(pick);
    if (new Set(names).size === names.length)
      return new Map(lanes.map((l, i) => [l.id, names[i]]));
  }
  return new Map(lanes.map((l) => [l.id, `${full(l)} (${l.label})`]));
}

const joinNames = (xs) =>
  xs.length <= 1
    ? xs[0] ?? ""
    : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;

/** `failed all 3 tries` · `passed 2 of 3 tries` · `didn't run this task`. */
export function outcomePhrase(verdict) {
  const v = verdict ?? {};
  const n = v.scored ?? 0;
  if (v.key === "production") return "is the production conversation";
  if (v.key === "not_run" || v.key === "infra" || !n)
    return "didn't run this task";
  if (v.key === "error")
    return n === 1 ? "errored" : `errored on all ${n} tries`;
  if (v.pass === n) return n === 1 ? "passed" : `passed all ${n} tries`;
  if (!v.pass) return n === 1 ? "failed" : `failed all ${n} tries`;
  return `passed ${v.pass} of ${n} tries`;
}

/**
 * Who did what, grouped by outcome, baseline first.
 * @param {{lanes: {id, verdict}[], names: Map, baselineId?: string}} args
 * @returns {{text: string, parts: {ids: string[], phrase: string}[]}}
 */
export function resultSummary({ lanes = [], names, baselineId = null } = {}) {
  const parts = [];
  lanes.forEach((l) => {
    const phrase = outcomePhrase(l.verdict);
    const hit = parts.find((p) => p.phrase === phrase);
    if (hit) hit.ids.push(l.id);
    else parts.push({ ids: [l.id], phrase });
  });
  const nameOf = (id) =>
    `${names?.get(id) ?? id}${id === baselineId ? " (baseline)" : ""}`;
  const text = `${parts
    .map((p) => `${joinNames(p.ids.map(nameOf))} ${p.phrase}`)
    .join("; ")}.`;
  return { text, parts };
}

/** `passed` · `passed all 3` · `failed all 3` · `passed 2 of 3`. */
function shortOutcome(verdict) {
  const v = verdict ?? {};
  const n = v.scored ?? 0;
  if (v.key === "production") return "is the production conversation";
  if (v.key === "not_run" || v.key === "infra" || !n) return "didn't run";
  if (v.key === "error") return n === 1 ? "errored" : `errored all ${n}`;
  if (v.pass === n) return n === 1 ? "passed" : `passed all ${n}`;
  if (!v.pass) return n === 1 ? "failed" : `failed all ${n}`;
  return `passed ${v.pass} of ${n}`;
}

const RANK = (v) =>
  v?.key === "production"
    ? 3
    : !v?.scored
      ? 4
      : v.pass === v.scored
        ? 0
        : v.pass
          ? 1
          : 2;

// The lane header's step count (workspaceModel.trialMetrics: every step,
// the terminal one included). Every "took N steps" sentence uses it, so the
// summary and the why never print two counts for one run (story beats fold
// steps: 7 beats ≠ 15 steps).
const laneStepCount = (l) => (l.trial?.steps ?? l.steps ?? []).length;

// The why when every run passed; it only restates the summary's counts.
const ALL_PASSED = "Every run passed";

/**
 * IT-R2 G4 P8 · the one sentence above the steps: who took how many steps
 * and how it ended, winners first, the why on the run it is about —
 * `the coding harness Code took 16 steps and passed all 3; Support Agent v3 took 7 and
 * failed all 3: dropped "A-" from the order id in its first step, so its
 * "Order found" check failed.` Pure.
 * @param {{lanes: {id, verdict, steps?}[], names: Map, why?: string}} a
 * @returns {string}
 */
export function summarySentence({ lanes = [], names, why } = {}) {
  if (!lanes.length) return why ?? "";
  const nameOf = (l) => names?.get(l.id) ?? l.label ?? "A run";
  const whyText = String(why ?? "").trim();
  const lead = [...lanes]
    .filter((l) => whyText.startsWith(`${nameOf(l)} `))
    .sort((a, b) => nameOf(b).length - nameOf(a).length)[0];
  // The why belongs to one run only when it names no other run (a call
  // row's why compares the calls: it follows the sentence instead).
  const owner =
    lead &&
    !lanes.some(
      (l) =>
        l.id !== lead.id &&
        whyText.slice(nameOf(lead).length).includes(nameOf(l)),
    )
      ? lead
      : null;
  let stepsSaid = false;
  const parts = [...lanes]
    .map((l, i) => ({ l, i }))
    .sort((a, b) => RANK(a.l.verdict) - RANK(b.l.verdict) || a.i - b.i)
    .map(({ l }) => {
      const n = laneStepCount(l);
      const took = n
        ? ` took ${n}${stepsSaid ? "" : n === 1 ? " step" : " steps"} and`
        : "";
      if (n) stepsSaid = true;
      let part = `${nameOf(l)}${took} ${shortOutcome(l.verdict)}`;
      if (owner && l.id === owner.id) {
        const clause = whyText
          .slice(nameOf(l).length + 1)
          .replace(/\.\s*$/, "");
        if (clause) part += `: ${clause}`;
      }
      return part;
    });
  const sentence = `${parts.join("; ")}.`;
  // Every run passed: the counts above already say who took more steps.
  const restates = stepsSaid && whyText.startsWith(ALL_PASSED);
  return owner || !whyText || restates ? sentence : `${sentence} ${whyText}`;
}

const cellSteps = (cell) => {
  if (!cell) return [];
  if (cell.type === "gap") return cell.steps ?? [];
  return [...(cell.subSteps ?? []), cell.step].filter(Boolean);
};

/** `Looked up order 7781 — not found (404)` → action + result. */
export function splitBeat(text) {
  const s = String(text ?? "");
  const read = /^Read the order id as ("[^"]+")(?:\.\s*(.*))?$/.exec(s);
  if (read)
    return {
      action: "Read the order id",
      result: read[1],
      extra: read[2] || null,
    };
  const i = s.indexOf(" — ");
  if (i > 0)
    return { action: s.slice(0, i), result: s.slice(i + 3), extra: null };
  return { action: s, result: null, extra: null };
}

/** Beats of one lane cell (hand-authored story wins, else templates). */
function beatsOf(cell, lane, ctx) {
  const steps = cellSteps(cell);
  if (!steps.length) return [];
  if (lane?.story?.length) {
    const ns = new Set(steps.map((s) => s.n));
    return lane.story
      .filter((b) => ns.has(b.step_n))
      .map((b) => ({
        text: b.text,
        stepN: b.step_n,
        tone: b.tone ?? "neutral",
      }));
  }
  return steps
    .map((s) => {
      if (s.kind === "think") {
        const text = thinkBeat(s);
        return text ? { text, stepN: s.n, tone: "neutral" } : null;
      }
      if (isChatter(s, s.n === ctx.lastReplyN)) return null;
      const text = milestoneBeat(s, { firstCustomer: !ctx.seenCustomer });
      if (s.kind === "customer") ctx.seenCustomer = true;
      return text
        ? { text, stepN: s.n, tone: s.error ? "bad" : "neutral" }
        : null;
    })
    .filter(Boolean);
}

const isKey = (b) =>
  b.firstWrong || b.failed.length || b.tone === "bad" || b.shared;

/** Tool-like steps compare by tool name (`issue_refund`); chat does not. */
const ACTION_KINDS = new Set([
  "tool",
  "retrieval",
  "file_edit",
  "command",
  "test",
  "subagent",
]);
const actionKey = (s) =>
  s && ACTION_KINDS.has(s.kind) ? `${s.kind}:${s.toolName ?? s.name}` : null;

/**
 * The grid.
 * @param {{rows, lanes: [{id, story?, checks?}], firstWrong?: {laneId,
 *   stepN, rowId}}} args  lanes base first, the same order as row cells
 * @returns {{items: ({type: "row", id, cells: {beats}[]} | {type: "fold",
 *   id, lane: number, count: number, rows: object[]})[], firstWrongBeat:
 *   {lane, seq, beat, rowId}|null, beatCount: number[]}}
 */
export function storyGrid({ rows = [], lanes = [], firstWrong = null } = {}) {
  const ctx = lanes.map((_, l) => {
    let lastReplyN = null;
    rows.forEach((r) =>
      cellSteps(r.cells[l]).forEach((s) => {
        if (s.kind === "reply") lastReplyN = s.n;
      }),
    );
    return { lastReplyN, seenCustomer: false };
  });
  // Actions each lane took anywhere (a refund in two runs never folds, even
  // when the baseline-relative alignment put them on different rows).
  const acts = lanes.map(
    (_, l) =>
      new Set(
        rows
          .flatMap((r) => cellSteps(r.cells[l]).map(actionKey))
          .filter(Boolean),
      ),
  );
  const seq = lanes.map(() => 0);
  const built = rows
    .map((r) => {
      const cells = lanes.map((lane, l) => {
        const steps = cellSteps(r.cells[l]);
        const beats = beatsOf(r.cells[l], lane, ctx[l]).map((b) => {
          seq[l] += 1;
          const step = steps.find((s) => s.n === b.stepN);
          const key = actionKey(step);
          const shared =
            Boolean(key) && acts.some((set, i) => i !== l && set.has(key));
          return {
            ...b,
            kind: step?.kind ?? null,
            seq: seq[l],
            failed: [],
            firstWrong: false,
            shared,
            key,
            rowId: r.id,
          };
        });
        return { beats };
      });
      return { type: "row", id: r.id, cells };
    })
    .filter((r) => r.cells.some((c) => c.beats.length));

  // A row only one run has: one row per step, so folds and the same-action
  // merge below work step by step.
  for (let i = built.length - 1; i >= 0; i -= 1) {
    const r = built[i];
    const ls = r.cells.filter((c) => c.beats.length);
    if (ls.length !== 1 || ls[0].beats.length < 2) continue;
    const l = r.cells.indexOf(ls[0]);
    const parts = ls[0].beats.map((b, k) => ({
      type: "row",
      id: `${r.id}.${k}`,
      cells: r.cells.map((c, o) => ({ beats: o === l ? [b] : [] })),
    }));
    built.splice(i, 1, ...parts);
  }

  // Same action, different rows (the alignment is against the baseline, so
  // two runs that both refund while the baseline never did can land apart):
  // move a lane's cell to the row where another run took the same action,
  // when that keeps the lane's own order (no beat of it in between).
  const keyOfBeat = (b) => b.key;
  const moveCell = (from, to, l) => {
    built[to].cells[l] = built[from].cells[l];
    built[from].cells[l] = { beats: [] };
  };
  const hasLane = (i, l) => built[i].cells[l].beats.length > 0;
  for (let i = 0; i < built.length; i += 1) {
    for (let l = 0; l < lanes.length; l += 1) {
      const first = built[i].cells[l].beats[0];
      const k = first && keyOfBeat(first);
      if (!k) continue;
      const already = built[i].cells.some(
        (c, o) => o !== l && c.beats.some((b) => b.key === k),
      );
      if (already) continue;
      const matches = (j) =>
        !hasLane(j, l) &&
        built[j].cells.some(
          (c, o) => o !== l && c.beats.some((b) => b.key === k),
        );
      let target = -1;
      for (let j = i - 1; j >= 0 && !hasLane(j, l); j -= 1)
        if (matches(j)) {
          target = j;
          break;
        }
      if (target < 0)
        for (let j = i + 1; j < built.length && !hasLane(j, l); j += 1)
          if (matches(j)) {
            target = j;
            break;
          }
      if (target >= 0) moveCell(i, target, l);
    }
  }
  for (let i = built.length - 1; i >= 0; i -= 1)
    if (!built[i].cells.some((c) => c.beats.length)) built.splice(i, 1);

  // First wrong step: its exact beat, else the next beat of that lane.
  let firstWrongBeat = null;
  const fl = firstWrong
    ? lanes.findIndex((l) => l.id === firstWrong.laneId)
    : -1;
  if (fl >= 0) {
    const all = built.flatMap((r) => r.cells[fl].beats.map((b) => [b, r.id]));
    const hit =
      all.find(([b]) => b.stepN === firstWrong.stepN) ??
      all.find(([b]) => b.stepN > firstWrong.stepN) ??
      null;
    if (hit) {
      hit[0].firstWrong = true;
      firstWrongBeat = {
        lane: fl,
        seq: hit[0].seq,
        beat: hit[0],
        rowId: hit[0].rowId,
      };
    }
  }
  // Failed checks sit on the beat of the step that caused them.
  lanes.forEach((lane, l) =>
    (lane.checks ?? [])
      .filter((c) => c.passed === false && Number.isFinite(c.cause_step_n))
      .forEach((chk) => {
        const all = built.flatMap((r) => r.cells[l].beats);
        const target =
          all.find((b) => b.stepN === chk.cause_step_n) ??
          [...all].reverse().find((b) => b.stepN <= chk.cause_step_n) ??
          all[all.length - 1];
        if (target && !target.failed.includes(chk.key))
          target.failed.push(chk.key);
      }),
  );

  // Fold runs of rows that only one lane has (≥ 2 steps, nothing key inside).
  const storied = lanes.filter((_, l) =>
    built.some((r) => r.cells[l].beats.length),
  ).length;
  const onlyLane = (r) => {
    const ls = r.cells
      .map((c, l) => (c.beats.length ? l : -1))
      .filter((l) => l >= 0);
    return ls.length === 1 ? ls[0] : -1;
  };
  const items = [];
  let run = null;
  // Inside a run only one lane has, re-chunk by beat: key beats stay as
  // rows, 2+ consecutive other beats fold.
  const flush = () => {
    if (!run) return;
    const L = run.lane;
    const mk = (bs) => ({
      type: "row",
      id: `o${L}-${bs[0].seq}`,
      cells: lanes.map((_, i) => ({ beats: i === L ? bs : [] })),
    });
    let show = [];
    let fold = [];
    const outShow = () => {
      if (show.length) items.push(mk(show));
      show = [];
    };
    const outFold = () => {
      if (fold.length >= 2)
        items.push({
          type: "fold",
          id: `f${L}-${fold[0].seq}`,
          lane: L,
          count: fold.length,
          rows: [mk(fold)],
        });
      else show.push(...fold);
      fold = [];
    };
    run.rows
      .flatMap((r) => r.cells[L].beats)
      .forEach((b) => {
        if (isKey(b)) {
          outFold();
          show.push(b);
        } else {
          outShow();
          fold.push(b);
        }
      });
    outFold();
    outShow();
    run = null;
  };
  built.forEach((r) => {
    const l = storied >= 2 ? onlyLane(r) : -1;
    if (l < 0) {
      flush();
      items.push(r);
      return;
    }
    if (run && run.lane !== l) flush();
    if (!run) run = { lane: l, rows: [] };
    run.rows.push(r);
  });
  flush();
  mergeOpeningMessage(items);
  return { items, firstWrongBeat, beatCount: seq };
}

/**
 * The customer's opening message: harness transcripts (the coding harness Code, the coding harness)
 * record it as their step 1, the other runs got it as input. Alone on the
 * first row it reads as an empty row in every other run, so it joins its
 * lane's cell in the next row (`1 Customer: …` above `2 Looked up …`).
 * Mutates `items`.
 */
function mergeOpeningMessage(items) {
  const [head, next] = items;
  if (head?.type !== "row" || next?.type !== "row") return;
  const cells = head.cells.filter((c) => c.beats.length);
  if (
    !cells.length ||
    cells.length === head.cells.length ||
    !cells.every((c) => c.beats.every((b) => b.kind === "customer"))
  )
    return;
  items.splice(0, 2, {
    ...next,
    cells: next.cells.map((c, l) => ({
      ...c,
      beats: [...head.cells[l].beats, ...c.beats],
    })),
  });
}

/**
 * One short line under the first wrong step: `should be "A-7781"` (what the
 * other runs read at the same aligned step) · `broke "Order found"`.
 */
export function firstWrongNote({ beat, row, laneIdx, checkName } = {}) {
  if (!beat) return null;
  const parts = [];
  const steps = cellSteps(row?.cells?.[laneIdx]);
  const mine =
    steps.find((s) => s.n === beat.stepN && orderIdOf(s)) ??
    steps.find((s) => orderIdOf(s));
  const id = orderIdOf(mine);
  if (id) {
    const theirs = (row?.cells ?? [])
      .filter((_, i) => i !== laneIdx)
      .flatMap(cellSteps)
      .map(orderIdOf)
      .find((v) => v && v !== id);
    if (theirs) parts.push(`should be "${theirs}"`);
  }
  const extra = splitBeat(beat.text).extra;
  if (!parts.length && extra) parts.push(extra.replace(/\.$/, ""));
  if (checkName) parts.push(`this broke "${checkName}"`);
  return parts.length ? parts.join(" · ") : "The run went off track here";
}

/**
 * `Why:` sentence for the summary card (mechanical, never AI prose).
 * @param {{grid, rows, lanes: [{id, checks, verdict, steps?}], names: Map,
 *   checkName: (key) => string}} args
 */
export function whyLine({
  grid,
  rows = [],
  lanes = [],
  names,
  checkName = (k) => k,
} = {}) {
  const nameOf = (l) => names?.get(l.id) ?? l.label ?? "A run";
  const fwb = grid?.firstWrongBeat;
  if (fwb) {
    const lane = lanes[fwb.lane];
    // The row's own step number (one numbering source with the steps list).
    const n = fwb.beat?.stepN ?? fwb.seq;
    // The end state is not a step the run took (the lane header and the
    // summary count 16 steps; the end state sits after them), so the line
    // says how the run ended, never `took 16 … at step 17`.
    const atEnd = fwb.beat?.kind === "end";
    const where = atEnd
      ? "at the end"
      : n === 1
        ? "in its first step"
        : `at step ${n}`;
    const failed = (lane.checks ?? []).find((c) => c.passed === false);
    const tail = failed
      ? `, so its "${checkName(failed.key)}" check failed`
      : "";
    const row = rows.find((r) => r.id === fwb.rowId);
    const steps = cellSteps(row?.cells?.[fwb.lane]);
    const id = steps.map(orderIdOf).find(Boolean);
    const theirs = id
      ? (row?.cells ?? [])
          .filter((_, i) => i !== fwb.lane)
          .flatMap(cellSteps)
          .map(orderIdOf)
          .find((v) => v && v !== id)
      : null;
    if (id && theirs) {
      const what =
        theirs.endsWith(id) && theirs.length > id.length
          ? `dropped "${theirs.slice(0, theirs.length - id.length)}" from the order id`
          : `read the order id as "${id}" instead of "${theirs}"`;
      return `${nameOf(lane)} ${what} ${where}${tail}.`;
    }
    const act = splitBeat(fwb.beat.text);
    const said = `${act.action.charAt(0).toLowerCase()}${act.action.slice(1)}${act.result ? ` (${act.result})` : ""}`;
    if (atEnd && /^ended\b/.test(said))
      return `${nameOf(lane)} ${said}${tail}.`;
    return `${nameOf(lane)} went wrong ${where}: ${said}${tail}.`;
  }
  const failing = lanes.find((l) =>
    (l.checks ?? []).some((c) => c.passed === false),
  );
  if (failing) {
    const c = failing.checks.find((x) => x.passed === false);
    return `${nameOf(failing)} failed the "${checkName(c.key)}" check${c.evidence ? ` (${c.evidence})` : ""}.`;
  }
  const counted = lanes
    .map((l, i) => ({
      ...l,
      // The header's count; story beats only when a lane has no steps.
      took: laneStepCount(l) || (grid?.beatCount?.[i] ?? 0),
    }))
    .filter((l) => l.verdict?.scored);
  if (
    counted.length >= 2 &&
    counted.every((l) => l.verdict.pass === l.verdict.scored)
  ) {
    const bySteps = [...counted].sort((a, b) => a.took - b.took);
    const lo = bySteps[0];
    const hi = bySteps[bySteps.length - 1];
    if (hi.took > lo.took)
      return `${ALL_PASSED}. They differ in effort: ${nameOf(lo)} took ${lo.took} ${lo.took === 1 ? "step" : "steps"}, ${nameOf(hi)} took ${hi.took}.`;
    return `${ALL_PASSED}, in the same number of steps.`;
  }
  return null;
}

/**
 * IT-R4 G4 P4 · what the other run did differently, from the step diff (not
 * prose): `the coding harness Code took 16 steps (Support Agent v3: 7): it checked
 * replacement stock first, which kept the "A-" prefix; the 9 extra steps cost
 * 17×.` The run is the first one whose result differs from the baseline
 * (lanes[0]); null when the runs did the same thing.
 * @param {{grid, rows?: object[], lanes: object[], names?: Map}} args
 * @returns {string|null}
 */
export function differentlyLine({ grid, rows = [], lanes = [], names } = {}) {
  if (lanes.length < 2 || !grid?.items) return null;
  const nameOf = (l) => names?.get(l.id) ?? l.label ?? "A run";
  const base = lanes[0];
  const outcome = (l) =>
    l.verdict?.scored ? (l.verdict.pass === l.verdict.scored ? 1 : 0) : null;
  let ci = lanes.findIndex((l, i) => i > 0 && outcome(l) !== outcome(base));
  if (ci < 0) ci = 1;
  const cand = lanes[ci];
  const took = laneStepCount(cand);
  const baseTook = laneStepCount(base);
  if (!took || !baseTook) return null;
  // The first steps only the other run took (tools first, then any).
  const only = [];
  grid.items.forEach((it) => {
    if (it.type === "fold") {
      if (it.lane === ci)
        it.rows.forEach((r) => only.push(...(r.cells[ci]?.beats ?? [])));
      return;
    }
    const mine = it.cells?.[ci]?.beats ?? [];
    const others = (it.cells ?? []).some((c, i) => i !== ci && c.beats?.length);
    if (mine.length && !others) only.push(...mine);
  });
  const firstOnly =
    only.find((b) => ACTION_KINDS.has(b.kind)) ??
    only.find((b) => b.kind !== "customer" && b.kind !== "reply");
  // Long quoted text (a search query) shortens to keep the line readable.
  const act = firstOnly
    ? splitBeat(firstOnly.text).action.replace(
        /"([^"]{22})[^"]{4,}"/g,
        (_, head) => `"${head.trimEnd()}…"`,
      )
    : null;
  // Kept the prefix the baseline dropped (the order-id evidence).
  const fwb = grid.firstWrongBeat;
  let kept = "";
  if (fwb && fwb.lane !== ci) {
    const row = rows.find((r) => r.id === fwb.rowId);
    const id = cellSteps(row?.cells?.[fwb.lane]).map(orderIdOf).find(Boolean);
    const theirs = rows
      .flatMap((r) => cellSteps(r.cells?.[ci]))
      .map(orderIdOf)
      .find((v) => v && v !== id);
    if (id && theirs && theirs.endsWith(id) && theirs.length > id.length)
      kept = `, and kept the "${theirs.slice(0, theirs.length - id.length)}" in the order id`;
  }
  const extra = took - baseTook;
  const c1 = cand.trial?.cost_usd;
  const c0 = base.trial?.cost_usd;
  const ratio =
    Number.isFinite(c1) && Number.isFinite(c0) && c0 > 0 ? c1 / c0 : null;
  const costPart =
    ratio && ratio >= 1.5
      ? `${extra > 0 ? `; the ${extra} extra ${extra === 1 ? "step" : "steps"}` : "; it"} cost ${ratio >= 10 ? Math.round(ratio) : ratio.toFixed(1)}×`
      : ratio && ratio <= 0.67
        ? `; it cost ${Math.round((1 - ratio) * 100)}% less`
        : "";
  if (!act && !kept && !costPart && extra === 0) return null;
  const lower = (s) => `${s.charAt(0).toLowerCase()}${s.slice(1)}`;
  return `${nameOf(cand)} took ${took} ${took === 1 ? "step" : "steps"} (${nameOf(base)}: ${baseTook})${act ? `: it ${lower(act)}` : ""}${kept}${costPart}.`;
}
