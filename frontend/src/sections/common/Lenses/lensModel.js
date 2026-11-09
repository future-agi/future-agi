// Pure models behind the modality lenses (R4 lane M): turn alignment,
// the first call difference, WER and word diffs, stage splits, the latency
// rows, loudness gain, an image difference and nearest-rank percentiles.
// No React, no DOM. Every number a lens prints comes from here or from the
// call value itself (`call.metrics`, computed by the world from the turns).
//
// Sentence templates here carry no digit literals (R4 common §C10): every
// number is computed from the values passed in.

// ------------------------------------------------------------ words

const DIGIT_WORDS = [
  "zero",
  "one",
  "two",
  "three",
  "four",
  "five",
  "six",
  "seven",
  "eight",
  "nine",
];

/**
 * Normalised words for WER: accents, case and punctuation off, digits
 * spelled one by one (`8830` = `eight eight three zero`, as a caller says it).
 */
export function werWords(text) {
  return String(text ?? "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/(\d)/g, " $1 ")
    .replace(/['’]/g, "")
    .replace(/[^a-z0-9ñ ]+/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => (/^\d$/.test(w) ? DIGIT_WORDS[Number(w)] : w));
}

/** Levenshtein over word arrays → edit count. */
function editDistance(a, b) {
  let prev = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i += 1) {
    const cur = [i];
    for (let j = 1; j <= b.length; j += 1)
      cur[j] = Math.min(
        prev[j] + 1,
        cur[j - 1] + 1,
        prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1),
      );
    prev = cur;
  }
  return prev[b.length];
}

/**
 * Word error rate of a hypothesis against a reference.
 * @returns {{edits: number, words: number, wer: number|null}}
 */
export function wer(ref, hyp) {
  const a = werWords(ref);
  const b = werWords(hyp);
  const edits = editDistance(a, b);
  return { edits, words: a.length, wer: a.length ? edits / a.length : null };
}

/** Corpus WER over a call's caller turns (scripted vs heard). */
export function callWer(call) {
  let edits = 0;
  let words = 0;
  (call?.turns ?? []).forEach((t) => {
    if (t.role !== "caller" || t.scripted_text == null) return;
    const w = wer(t.scripted_text, t.text);
    edits += w.edits;
    words += w.words;
  });
  return { edits, words, wer: words ? edits / words : null };
}

const tokensOf = (text) => String(text ?? "").match(/\S+/g) ?? [];
const keyOf = (token) => werWords(token).join(" ");

/**
 * A word diff of what was said (`ref`) and what was heard (`hyp`), on the
 * original tokens: `[{text, op: "same"|"missing"|"extra"}]` (a substitution
 * is a missing word followed by an extra one). Compared on normalised words,
 * so `8830` matches `eight eight three zero` and `A,` matches `A`.
 */
export function wordDiff(ref, hyp) {
  const expand = (tokens) =>
    tokens.flatMap((tok, ti) => {
      const ws = werWords(tok);
      return ws.length ? ws.map((w) => ({ w, ti })) : [{ w: tok, ti }];
    });
  const ta = tokensOf(ref);
  const tb = tokensOf(hyp);
  const a = expand(ta);
  const b = expand(tb);
  const n = a.length;
  const m = b.length;
  const L = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i -= 1)
    for (let j = m - 1; j >= 0; j -= 1)
      L[i][j] =
        a[i].w === b[j].w
          ? L[i + 1][j + 1] + 1
          : Math.max(L[i + 1][j], L[i][j + 1]);
  const ops = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i].w === b[j].w) {
      ops.push(["same", b[j].ti]);
      i += 1;
      j += 1;
    } else if (L[i + 1][j] >= L[i][j + 1]) {
      ops.push(["missing", a[i].ti]);
      i += 1;
    } else {
      ops.push(["extra", b[j].ti]);
      j += 1;
    }
  }
  while (i < n) ops.push(["missing", a[i++].ti]);
  while (j < m) ops.push(["extra", b[j++].ti]);
  // Back to tokens: a heard token is `same` when every word of it matched.
  const hypMatched = tb.map(() => ({ same: 0, total: 0 }));
  b.forEach((x) => {
    hypMatched[x.ti].total += 1;
  });
  ops.forEach(([op, ti]) => {
    if (op === "same") hypMatched[ti].same += 1;
  });
  const seenRef = new Set();
  const seenHyp = new Set();
  const out = [];
  ops.forEach(([op, ti]) => {
    if (op === "missing") {
      if (seenRef.has(ti)) return;
      seenRef.add(ti);
      out.push({ text: ta[ti], op: "missing" });
      return;
    }
    if (seenHyp.has(ti)) return;
    seenHyp.add(ti);
    const h = hypMatched[ti];
    out.push({ text: tb[ti], op: h.same === h.total ? "same" : "extra" });
  });
  return out;
}

/** Share of words that changed between two replies (0–1). */
export function replyChange(a, b) {
  const x = werWords(a);
  const y = werWords(b);
  const base = Math.max(x.length, y.length);
  if (!base) return 0;
  return editDistance(x, y) / base;
}

// ------------------------------------------------------------ turns

const isCaller = (t) => t?.role === "caller";
const isAgent = (t) => t?.role === "agent";

/**
 * A call's turns grouped in pairs: pair 0 = the agent's turns before the
 * caller first speaks (the greeting); pair k = the k-th caller turn and
 * the agent turns that follow it. Tool turns (`role: "tool"`, older
 * shapes) ride on the pair they fall in.
 * @returns {{caller: object|null, agents: object[], tools: object[]}[]}
 */
export function pairsOf(call) {
  const pairs = [{ caller: null, agents: [], tools: [] }];
  (call?.turns ?? []).forEach((t) => {
    if (isCaller(t)) pairs.push({ caller: t, agents: [], tools: [] });
    else if (isAgent(t)) pairs[pairs.length - 1].agents.push(t);
    else pairs[pairs.length - 1].tools.push(t);
  });
  if (!pairs[0].agents.length && !pairs[0].tools.length && pairs.length > 1)
    pairs[0].empty = true;
  return pairs;
}

/** The tool calls of a pair, in order (`turn.tool` or tool turns). */
export function toolCallsOf(pair) {
  const fromAgents = (pair?.agents ?? [])
    .filter((t) => t.tool)
    .map((t) => ({ ...t.tool, turn: t.i }));
  const fromTools = (pair?.tools ?? [])
    .filter((t) => t.tool)
    .map((t) => ({ ...t.tool, turn: t.i }));
  return [...fromAgents, ...fromTools].sort((x, y) => x.turn - y.turn);
}

const argsKey = (tool) =>
  `${tool?.name ?? ""}(${JSON.stringify(tool?.args ?? {})})`;

/**
 * Align 2–4 calls by caller-turn index (unequal turn counts allowed).
 * @param {(object|null)[]} calls  baseline first
 * @returns {{k: number, lanes: ({caller, agents, tools}|null)[]}[]}
 */
export function alignCalls(calls) {
  const all = (calls ?? []).map((c) => (c ? pairsOf(c) : null));
  const n = Math.max(0, ...all.map((p) => (p ? p.length : 0)));
  const rows = [];
  for (let k = 0; k < n; k += 1) {
    const lanes = all.map((p) => (p && p[k] ? p[k] : null));
    if (k === 0 && lanes.every((l) => !l || l.empty)) continue;
    rows.push({ k, lanes });
  }
  return rows;
}

/**
 * The navigation steps of aligned rows: per row the caller slot (0) and
 * each agent slot (1…). `n` is the turn number shown (the baseline's `i`,
 * else the first lane that has the slot).
 * @returns {{id: string, k: number, slot: number, n: number|null,
 *   role: "caller"|"agent"}[]}
 */
export function stepsOf(rows) {
  const steps = [];
  rows.forEach((row) => {
    const maxAgents = Math.max(
      0,
      ...row.lanes.map((l) => (l ? l.agents.length : 0)),
    );
    const slots = [];
    if (row.lanes.some((l) => l?.caller)) slots.push(0);
    for (let s = 1; s <= maxAgents; s += 1) slots.push(s);
    slots.forEach((slot) => {
      const turnIn = (l) =>
        !l ? null : slot === 0 ? l.caller : l.agents[slot - 1] ?? null;
      const first = row.lanes.map(turnIn).find(Boolean);
      steps.push({
        id: `${row.k}.${slot}`,
        k: row.k,
        slot,
        n: first?.i ?? null,
        role: slot === 0 ? "caller" : "agent",
      });
    });
  });
  return steps;
}

/** A lane's turn for a step (or the nearest one in that pair). */
export function laneTurnAt(row, laneIndex, slot) {
  const l = row?.lanes?.[laneIndex];
  if (!l) return null;
  if (slot === 0) return l.caller ?? l.agents[0] ?? null;
  return l.agents[slot - 1] ?? l.agents[l.agents.length - 1] ?? l.caller;
}

/**
 * Where each lane should be for a step (seconds): its own matching turn's
 * start (Align on turns), or past its end when the lane has no such pair.
 * @returns {(number|null)[]}
 */
export function seekTimesFor(rows, step, calls) {
  const row = rows.find((r) => r.k === step?.k);
  return (calls ?? []).map((call, i) => {
    if (!call) return null;
    const t = row ? laneTurnAt(row, i, step.slot) : null;
    if (t && Number.isFinite(t.start_ms)) return t.start_ms / 1000;
    return Number.isFinite(call.duration_ms) ? call.duration_ms / 1000 : null;
  });
}

// ------------------------------------------------------------ first difference

const firstPrefixDrop = (said, heard) => {
  const a = tokensOf(said);
  const b = tokensOf(heard);
  let p = 0;
  while (p < a.length && p < b.length && keyOf(a[p]) === keyOf(b[p])) p += 1;
  const clean = (xs) =>
    xs
      .join(" ")
      .replace(/[.!?,;:]+$/, "")
      .trim();
  return { said: clean(a.slice(p)), heard: clean(b.slice(p)) };
};

/**
 * The first turn where a lane departs from the baseline: tool-call args
 * differ, the agent reply differs by more than `replyShare` of its words,
 * or one lane ended (greetings, pair 0, are not compared as replies: they
 * differ between agents by design). When the caller line of that row was heard
 * differently by that lane, the sentence names what was said and heard.
 * @param {(object|null)[]} calls  baseline first
 * @param {{names?: string[], callerName?: string, replyShare?: number}} [opts]
 * @returns {{k: number, turn: number, laneIndex: number,
 *   kind: "tool_args"|"reply"|"ended", text: string,
 *   said?: string, heard?: string}|null}
 */
export function firstCallDifference(calls, opts = {}) {
  const { names = [], callerName = "The caller", replyShare = 0.3 } = opts;
  const rows = alignCalls(calls);
  const nameOf = (i) => names[i] ?? `Lane ${String.fromCharCode(65 + i)}`;
  for (const row of rows) {
    const base = row.lanes[0];
    for (let j = 1; j < row.lanes.length; j += 1) {
      if (!calls[j]) continue;
      const lane = row.lanes[j];
      let kind = null;
      if (!base !== !lane) kind = "ended";
      else if (base && lane) {
        const bt = toolCallsOf(base).map(argsKey);
        const lt = toolCallsOf(lane).map(argsKey);
        if (bt.join("|") !== lt.join("|")) kind = "tool_args";
        else if (row.k > 0) {
          // The greeting (pair 0) differs between agents by design.
          const ba = base.agents.map((t) => t.text).join(" ");
          const la = lane.agents.map((t) => t.text).join(" ");
          if (replyChange(ba, la) > replyShare) kind = "reply";
        }
      }
      if (!kind) continue;
      const callerTurn = (lane ?? base)?.caller;
      const heardDiff =
        lane?.caller &&
        lane.caller.scripted_text != null &&
        wer(lane.caller.scripted_text, lane.caller.text).edits > 0;
      if (heardDiff) {
        const { said, heard } = firstPrefixDrop(
          lane.caller.scripted_text,
          lane.caller.text,
        );
        return {
          k: row.k,
          turn: lane.caller.i,
          laneIndex: j,
          kind,
          said,
          heard,
          text: `First difference: turn ${lane.caller.i} · ${callerName} said "${said}"; ${nameOf(j)} heard "${heard}"`,
        };
      }
      if (kind === "ended") {
        const who = lane ? nameOf(0) : nameOf(j);
        const at = (base ?? lane)?.caller?.i ?? callerTurn?.i ?? 0;
        return {
          k: row.k,
          turn: at,
          laneIndex: j,
          kind,
          text: `First difference: turn ${at} · ${who} had already ended the call`,
        };
      }
      if (kind === "tool_args") {
        const bt = toolCallsOf(base);
        const lt = toolCallsOf(lane);
        const idx = Math.max(
          0,
          bt.findIndex((t, x) => argsKey(t) !== argsKey(lt[x])),
        );
        const b = bt[idx];
        const l = lt[idx];
        const turn = (l ?? b)?.turn ?? lane.agents[0]?.i;
        const show = (t) =>
          t ? `${t.name}(${JSON.stringify(t.args ?? {})})` : "no tool call";
        return {
          k: row.k,
          turn,
          laneIndex: j,
          kind,
          text: `First difference: turn ${turn} · ${nameOf(0)} called ${show(b)}; ${nameOf(j)} called ${show(l)}`,
        };
      }
      const turn = lane.agents[0]?.i ?? base.agents[0]?.i;
      return {
        k: row.k,
        turn,
        laneIndex: j,
        kind,
        text: `First difference: turn ${turn} · ${nameOf(j)} replied differently from ${nameOf(0)}`,
      };
    }
  }
  return null;
}

// IT-R1 G4: the first difference RANKED by outcome (Call header, Why line).
const PAST = {
  issue: "issued",
  offer: "offered",
  lookup: "looked up",
  look: "looked",
  create: "created",
  cancel: "cancelled",
  escalate: "escalated",
  book: "booked",
  update: "updated",
  send: "sent",
  search: "searched",
  check: "checked",
  transfer: "transferred",
  schedule: "scheduled",
  change: "changed",
  refund: "refunded",
  get: "got",
  find: "found",
  verify: "verified",
  open: "opened",
  close: "closed",
  end: "ended",
};

/** `issue_refund` → `issued a refund`; unknown verbs → `called <name>`. */
export function toolActionPhrase(name) {
  const parts = String(name ?? "")
    .split(/[_\s]+/)
    .filter(Boolean);
  const verb = PAST[(parts[0] ?? "").toLowerCase()];
  if (!verb || parts.length < 2) return `called ${name ?? "a tool"}`;
  const obj = parts.slice(1).join(" ").toLowerCase();
  const article = /^[aeiou]/.test(obj) ? "an" : "a";
  return `${verb} ${article} ${obj}`;
}

const DIFF_RANK = { tool: 0, tool_args: 1, interruption: 2 };
const keptTalking = (pair) =>
  (pair?.agents ?? []).some((t) => t.interrupted_by === "caller");

/**
 * The first difference that matters most: a different tool (what the call
 * did) beats different tool arguments, which beat an interruption, which
 * beats wording (then firstCallDifference's rules: ended / reply / heard).
 * Ties go to the earliest turn.
 * @param {(object|null)[]} calls  baseline first
 * @param {{names?: string[], callerName?: string, replyShare?: number}} [opts]
 * @returns {{k, turn, laneIndex, kind: "tool"|"tool_args"|"interruption"|
 *   "reply"|"ended", text, short?: string, said?, heard?}|null}
 */
export function rankedCallDifference(calls, opts = {}) {
  const { names = [] } = opts;
  const nameOf = (i) => names[i] ?? `Lane ${String.fromCharCode(65 + i)}`;
  let best = null;
  alignCalls(calls).forEach((row) => {
    const base = row.lanes[0];
    for (let j = 1; j < row.lanes.length; j += 1) {
      const lane = row.lanes[j];
      if (!calls[j] || !base || !lane) continue;
      const bt = toolCallsOf(base);
      const lt = toolCallsOf(lane);
      let kind = null;
      if (bt.map((t) => t.name).join("|") !== lt.map((t) => t.name).join("|"))
        kind = "tool";
      else if (bt.map(argsKey).join("|") !== lt.map(argsKey).join("|"))
        kind = "tool_args";
      else if (row.k > 0 && keptTalking(base) !== keptTalking(lane))
        kind = "interruption";
      if (!kind) continue;
      if (!best || DIFF_RANK[kind] < DIFF_RANK[best.kind])
        best = { kind, row, j, base, lane, bt, lt };
    }
  });
  if (!best) return firstCallDifference(calls, opts);
  const { kind, row, j, base, lane, bt, lt } = best;
  if (kind === "interruption") {
    const who = keptTalking(base) ? 0 : j;
    const t = (who ? lane : base).agents.find(
      (a) => a.interrupted_by === "caller",
    );
    const turn = t?.i ?? lane.agents[0]?.i;
    const short = `${nameOf(who)} kept talking over the caller`;
    return {
      k: row.k,
      turn,
      laneIndex: j,
      kind,
      short,
      text: `First difference: turn ${turn} · ${short}`,
    };
  }
  const idx = Math.max(
    0,
    bt.findIndex((t, x) =>
      kind === "tool" ? t.name !== lt[x]?.name : argsKey(t) !== argsKey(lt[x]),
    ),
  );
  const b = bt[idx] ?? null;
  const l = lt[idx] ?? null;
  const turn = (l ?? b)?.turn ?? lane.agents[0]?.i;
  const show =
    kind === "tool"
      ? (t) =>
          t ? `${toolActionPhrase(t.name)} (${t.name})` : "called no tool"
      : (t) =>
          t
            ? `called ${t.name}(${JSON.stringify(t.args ?? {})})`
            : "called no tool";
  const short = `${nameOf(0)} ${show(b)}, ${nameOf(j)} ${show(l)}`;
  return {
    k: row.k,
    turn,
    laneIndex: j,
    kind,
    short,
    text: `First difference: turn ${turn} · ${short}`,
  };
}

/**
 * IT-R5 G4 P5: why a lane went wrong, from the heard diff plus the tool
 * result: `Acme voice v2 heard "8830" for "A-8830", so lookup_order
 * returned 404`. The first failed tool call of a non-baseline lane whose
 * arguments differ from the baseline's call of that tool; `heard` when a
 * caller line before it was heard differently, else `used`. Null when no
 * lane has such a call.
 * @param {(object|null)[]} calls  baseline first
 * @param {{names?: string[]}} [opts]
 * @returns {{laneIndex: number, turn: number, text: string}|null}
 */
export function callCause(calls, opts = {}) {
  const { names = [] } = opts;
  const nameOf = (i) => names[i] ?? `Lane ${String.fromCharCode(65 + i)}`;
  const rows = alignCalls(calls);
  const baseTools = rows.flatMap((r) => toolCallsOf(r.lanes[0]));
  for (let j = 1; j < (calls ?? []).length; j += 1) {
    if (!calls[j]) continue;
    let heardBefore = false;
    for (const row of rows) {
      const lane = row.lanes[j];
      if (!lane) continue;
      const c = lane.caller;
      if (c?.scripted_text != null && wer(c.scripted_text, c.text).edits > 0)
        heardBefore = true;
      const bad = toolCallsOf(lane).find((t) => t.status === "error");
      if (!bad) continue;
      const good = baseTools.find(
        (t) => t.name === bad.name && t.status !== "error",
      );
      const key = Object.keys(bad.args ?? {}).find(
        (k) => good && String(good.args?.[k]) !== String(bad.args?.[k]),
      );
      const result = toolChipText(bad).split(" → ")[1] ?? "an error";
      const what =
        key != null
          ? `${heardBefore ? "heard" : "used"} "${bad.args[key]}" for "${good.args[key]}", so `
          : "";
      return {
        laneIndex: j,
        turn: bad.turn,
        text: `${nameOf(j)} ${what}${bad.name} returned ${result}`,
      };
    }
  }
  return null;
}

// ------------------------------------------------------------ latency

/** A turn's stage split; `ok` when the stages add up to the gap. */
export function stageSplit(turn) {
  const s = turn?.stage_ms;
  const gap = turn?.gap_ms;
  if (!s || !Number.isFinite(gap))
    return { stt: null, llm: null, tts: null, total: gap ?? null, ok: false };
  const sum = (s.stt ?? 0) + (s.llm ?? 0) + (s.tts ?? 0);
  return { stt: s.stt, llm: s.llm, tts: s.tts, total: gap, ok: sum === gap };
}

/** Nearest-rank percentile (`q` 0–1) of finite values, or null. */
export function nearestRank(values, q) {
  const xs = (values ?? [])
    .filter((v) => Number.isFinite(v))
    .sort((a, b) => a - b);
  if (!xs.length) return null;
  return xs[Math.max(0, Math.ceil(q * xs.length) - 1)];
}

/** Every agent response gap of a call (ms). */
export const gapsOf = (call) =>
  (call?.turns ?? [])
    .filter((t) => isAgent(t) && Number.isFinite(t.gap_ms))
    .map((t) => t.gap_ms);

/**
 * The latency timeline's rows: one per aligned pair whose agent answered
 * (has a response gap) in any lane.
 * @returns {{k: number, n: number, lanes: ({gap, stage, interrupted:
 *   {stopMs}|null, deadAir: boolean, tools: object[], turn: number}|null)[]}[]}
 */
export function latencyRows(calls, { deadAirMs = 2000 } = {}) {
  const rows = alignCalls(calls);
  const out = [];
  rows.forEach((row) => {
    const lanes = row.lanes.map((l, i) => {
      if (!l || !calls[i] || calls[i].depth === "text_only") return null;
      const answer = l.agents.find((t) => Number.isFinite(t.gap_ms));
      if (!answer) return null;
      const kept = l.agents.find((t) => t.interrupted_by === "caller");
      return {
        turn: answer.i,
        gap: answer.gap_ms,
        stage: stageSplit(answer),
        interrupted: kept
          ? { stopMs: kept.agent_stop_ms ?? null, turn: kept.i }
          : null,
        deadAir: answer.gap_ms > deadAirMs,
        tools: toolCallsOf(l),
      };
    });
    if (!lanes.some(Boolean)) return;
    const n = lanes[0]?.turn ?? lanes.find(Boolean).turn;
    out.push({ k: row.k, n, lanes });
  });
  return out;
}

/**
 * The slowest response across lanes: the row holding the longest gap.
 * @returns {{k: number, n: number, laneIndex: number, gaps: (number|null)[]}|null}
 */
export function slowestTurn(rows) {
  let best = null;
  (rows ?? []).forEach((row) =>
    row.lanes.forEach((l, i) => {
      if (l && (!best || l.gap > best.gap))
        best = { k: row.k, n: l.turn, laneIndex: i, gap: l.gap, row };
    }),
  );
  if (!best) return null;
  return {
    k: best.k,
    n: best.n,
    laneIndex: best.laneIndex,
    gaps: best.row.lanes.map((l) => (l ? l.gap : null)),
  };
}

// ------------------------------------------------------------ loudness

/** RMS of waveform peaks (0–1), or null. */
export function peaksRms(peaks) {
  const xs = (peaks ?? []).filter((v) => Number.isFinite(v));
  if (!xs.length) return null;
  return Math.sqrt(xs.reduce((s, v) => s + v * v, 0) / xs.length);
}

/**
 * The gain that brings `peaks` to the loudness of `refPeaks`: `{db, linear}`
 * (`linear` capped at 1 for a media element, so a louder lane is turned
 * down rather than a quieter one up). null when either is silent.
 */
export function loudnessGain(peaks, refPeaks) {
  const a = peaksRms(peaks);
  const r = peaksRms(refPeaks);
  if (!a || !r) return null;
  const db = 20 * Math.log10(r / a);
  return { db, linear: Math.min(1, r / a) };
}

/**
 * Gains for every lane so all play at the quietest lane's loudness.
 * @param {(number[]|null)[]} peaksList
 * @returns {({db: number, linear: number}|null)[]}
 */
export function matchLoudness(peaksList) {
  const rms = peaksList.map(peaksRms);
  const quiet = Math.min(...rms.filter(Boolean));
  if (!Number.isFinite(quiet)) return peaksList.map(() => null);
  return rms.map((v) =>
    v ? { db: 20 * Math.log10(quiet / v), linear: quiet / v } : null,
  );
}

// ------------------------------------------------------------ images

/**
 * Absolute difference of two RGBA pixel arrays of the same size.
 * @param {Uint8ClampedArray|number[]} a
 * @param {Uint8ClampedArray|number[]} b
 * @param {{gain?: number, threshold?: number}} [opts]
 * @returns {{pixels: Uint8ClampedArray, changed: number, total: number,
 *   share: number}}
 */
export function imageDiff(a, b, { gain = 1, threshold = 24 } = {}) {
  const len = Math.min(a.length, b.length);
  const pixels = new Uint8ClampedArray(len);
  let changed = 0;
  const total = Math.floor(len / 4);
  for (let p = 0; p < len; p += 4) {
    const dr = Math.abs(a[p] - b[p]);
    const dg = Math.abs(a[p + 1] - b[p + 1]);
    const db = Math.abs(a[p + 2] - b[p + 2]);
    const d = Math.max(dr, dg, db);
    if (d > threshold) changed += 1;
    pixels[p] = Math.min(255, dr * gain);
    pixels[p + 1] = Math.min(255, dg * gain);
    pixels[p + 2] = Math.min(255, db * gain);
    pixels[p + 3] = 255;
  }
  return { pixels, changed, total, share: total ? changed / total : 0 };
}

/** Scale that fits `w × h` in a box, never past `maxUpscale` (default 2). */
export function fitScale(w, h, boxW, boxH, { maxUpscale = 2 } = {}) {
  if (!w || !h || !boxW || !boxH) return 1;
  return Math.min(boxW / w, boxH / h, maxUpscale);
}

// ------------------------------------------------------------ sentences
// (every line a lens prints about a call; numbers through the one formatter)

const argText = (v) => (typeof v === "string" ? `"${v}"` : JSON.stringify(v));

/** `lookup_order("8830") → 404` / `issue_refund("A-8830", 149) → ok`. */
export function toolChipText(tool) {
  if (!tool) return "";
  const args = Object.values(tool.args ?? {})
    .map(argText)
    .join(", ");
  let result = "ok";
  if (tool.status === "error") {
    const msg = String(tool.result?.error ?? tool.result ?? "error");
    const code = /^\d{3}\b/.exec(msg);
    result = code ? code[0] : "error";
  } else if (tool.result && typeof tool.result === "object") {
    result = String(
      tool.result.status ??
        tool.result.booking ??
        Object.values(tool.result)[0] ??
        "ok",
    );
  }
  return `${tool.name}(${args}) → ${result}`;
}

const STAGES = [
  ["stt", "STT"],
  ["llm", "LLM"],
  ["tts", "TTS"],
];

/**
 * The timeline tooltip of one row: `Turn 4 · Acme Support v1 1.6 s (STT
 * 330 ms · LLM 880 ms · TTS 390 ms) · Acme voice v2 700 ms (LLM −560 ms)`
 * (other lanes show the stage that changed most against the baseline).
 */
export function latencyTooltip(row, names, { fmtMs, fmtDeltaMs }) {
  const base = row.lanes[0];
  const parts = row.lanes.map((l, i) => {
    const name = names[i] ?? "";
    if (!l) return `${name} —`;
    const head = `${name} ${fmtMs(l.gap)}`;
    if (!l.stage.ok) return head;
    if (i === 0 || !base?.stage?.ok)
      return `${head} (${STAGES.map(([k, label]) => `${label} ${fmtMs(l.stage[k])}`).join(" · ")})`;
    const [k, label] = STAGES.reduce((best, s) =>
      Math.abs(l.stage[s[0]] - base.stage[s[0]]) >
      Math.abs(l.stage[best[0]] - base.stage[best[0]])
        ? s
        : best,
    );
    return `${head} (${label} ${fmtDeltaMs(l.stage[k] - base.stage[k])})`;
  });
  return `Turn ${row.n} · ${parts.join(" · ")}`;
}

/** `Slowest turn: 7 · Acme Support v1 2.6 s vs Acme voice v2 930 ms`. */
export function slowestLine(slow, names, { fmtMs }) {
  if (!slow) return null;
  const lead = `${names[slow.laneIndex] ?? ""} ${fmtMs(slow.gaps[slow.laneIndex])}`;
  // IT-R2 G4 P6: lanes with no gap there (text only) are left out, not `—`.
  const rest = slow.gaps
    .map((g, i) =>
      i === slow.laneIndex || g == null
        ? null
        : `${names[i] ?? ""} ${fmtMs(g)}`,
    )
    .filter(Boolean);
  return `Slowest turn ${slow.n}: ${[lead, ...rest].join(" vs ")}`;
}

/** The caller turn's tooltip: said, heard and the turn's WER. */
export function heardTooltip(turn, { fmtPct }) {
  if (!turn || turn.scripted_text == null) return null;
  const w = wer(turn.scripted_text, turn.text);
  const conf = Number.isFinite(turn.asr_confidence)
    ? ` · ASR confidence ${fmtPct(turn.asr_confidence)}`
    : "";
  return `Said: "${turn.scripted_text}" · Heard: "${turn.text}" · Turn WER ${fmtPct(w.wer)} (${w.edits}/${w.words})${conf}`;
}

/**
 * The 6 call metrics of one lane (from the call value's own metrics) and
 * which way is better. `null` = not measured (text only / no recording).
 */
export function callMetricsOf(call, { passed } = {}) {
  const m = call?.metrics ?? {};
  const textOnly = call?.depth === "text_only";
  const num = (v) => (Number.isFinite(v) ? v : null);
  return {
    task: passed === true ? 1 : passed === false ? 0 : null,
    duration: textOnly ? null : num(call?.duration_ms),
    p50: textOnly ? null : num(m.latency_ms?.p50),
    p90: textOnly ? null : num(m.latency_ms?.p90),
    kept: textOnly ? null : num(m.interruptions?.agent_kept_talking),
    bargeIns: textOnly ? null : num(m.interruptions?.caller_barge_ins),
    wer: textOnly ? null : num(m.wer),
    cost: num(m.cost_usd?.total),
  };
}

/** Which direction is better per metric (`duration` has none). */
export const METRIC_BETTER = Object.freeze({
  task: "higher",
  duration: null,
  p50: "lower",
  p90: "lower",
  kept: "lower",
  wer: "lower",
  cost: "lower",
});

/** `worse` / `better` / `same` / null for a lane value against the baseline. */
export function metricChange(key, value, base) {
  const dir = METRIC_BETTER[key];
  if (!dir || value == null || base == null) return null;
  if (value === base) return "same";
  const higher = value > base;
  return (dir === "higher") === higher ? "better" : "worse";
}
