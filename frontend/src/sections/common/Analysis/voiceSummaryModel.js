// Voice compare model (MUST 4 / 6; R4-SPEC D16; research
// voice-multimodal §2.2 P4–P6). Pure: every number on the voice screens
// (Matrix verdict, regression badges, persona rates, Summary `Speed and
// correctness`, results voice columns) is computed HERE from the raw calls of
// `GET …/voice-summary/` (handlers/r4-voice.js); task completion, deltas and
// better / worse rows go through F's `runNumbers.js` (D22). Sentence
// templates hold no digit literal (§C10); the regression threshold is the
// one constant.
//
// Rules:
// - latency = nearest-rank over every agent response gap of the run;
// - a candidate that answers faster but completes fewer calls never "beats":
//   the verdict is `mixed` and starts `Faster, but completes fewer calls:`;
// - a pinned metric that moves more than REGRESSION_THRESHOLD (relative) the
//   wrong way puts `▼ Worse on n metrics` on the candidate (Bluejay's 5 %).
import {
  EMPTY,
  fmtCost,
  fmtCount,
  fmtMs,
  fmtPct,
  fmtPts,
} from "src/sections/common/format/fmt";
import {
  compareRuns,
  diffRange95,
  runRate,
} from "src/sections/common/Runs/runNumbers";
import { verdictOf } from "src/sections/common/ux/verdictModel";

/**
 * The voice verdict follows the product's significance rule (R3 verdictOf:
 * the two 95 % ranges must not overlap) so every screen names the same pair
 * the same way: on 24 calls, +12.5 pts reads `is level with … (within
 * noise)`. One switch, so the owner can choose the brief's plain `beats`.
 */
export const VOICE_VERDICT_USES_SIGNIFICANCE = true;

/** Relative move that counts as a regression (5 %; configurable later). */
export const REGRESSION_THRESHOLD = 0.05;

const SECOND = 1000;
const TEN_SECONDS = 10 * SECOND;
const secFmt = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
/**
 * Voice latency: two decimals of a second under 10 s (`0.86 s`, `4.82 s`),
 * else fmt.fmtMs (`50 s`). Requested as a fmt.js option (integrator).
 */
export function fmtLatency(ms) {
  const n = Number(ms);
  if (ms == null || !Number.isFinite(n)) return EMPTY;
  if (Math.abs(n) >= TEN_SECONDS) return fmtMs(n);
  return `${secFmt.format(n / SECOND)} s`;
}

/** Nearest-rank percentile (q in 0–1) of a list; null when empty. */
export function nearestRank(values, q) {
  const xs = (values ?? [])
    .filter((v) => Number.isFinite(v))
    .sort((a, b) => a - b);
  if (!xs.length) return null;
  const rank = Math.max(1, Math.ceil(q * xs.length));
  return xs[Math.min(rank, xs.length) - 1];
}

const mean = (xs) => {
  const v = xs.filter((x) => Number.isFinite(x));
  return v.length ? v.reduce((s, x) => s + x, 0) / v.length : null;
};

/**
 * A run's name in sentences: the agent before ` · ` (`Acme voice v2`); a
 * text-only run keeps its depth (`Acme voice v2 · text only`).
 */
export function voiceName(run) {
  const name = String(run?.name ?? run?.label ?? "");
  const short = name.split(" · ")[0];
  return isTextOnly(run) && short !== name ? `${short} · text only` : short;
}

/** The voice runs of a summary, in its order. */
export const voiceRuns = (summary) =>
  (summary?.runs ?? []).filter((r) => r.voice);
export const isTextOnly = (run) => run?.depth === "text_only";

/**
 * A matrix (rows × runs, R3 shape) from the calls, so runNumbers computes
 * completion and better / worse exactly like every other screen.
 */
export function voiceMatrix(summary) {
  const rows = new Map();
  voiceRuns(summary).forEach((run) =>
    (run.calls ?? []).forEach((c) => {
      if (!rows.has(c.row_id))
        rows.set(c.row_id, {
          id: c.row_id,
          n: c.row_no,
          persona: c.persona,
          cells: {},
        });
      const row = rows.get(c.row_id);
      const cell = (row.cells[run.run_id] ??= { trials: [], cost_usd: [] });
      cell.trials.push(...c.trials);
      cell.cost_usd.push(c.cost_usd?.total ?? 0);
    }),
  );
  return {
    rows: [...rows.values()].sort((a, b) => a.n - b.n),
    columns: Object.fromEntries(
      voiceRuns(summary).map((r) => [r.run_id, { label: r.label }]),
    ),
  };
}

/** Per-call metrics (Matrix call cells, results voice columns). */
export function callStats(call, { keptMs } = {}) {
  if (!call) return null;
  const textOnly = call.depth === "text_only";
  const gaps = (call.gaps ?? []).map((g) => g.ms);
  const stops = call.barge_in_stops_ms ?? [];
  const kept = stops.filter((ms) => ms >= (keptMs ?? SECOND)).length;
  return {
    textOnly,
    passed: call.trials?.[0] === 1,
    p50: textOnly ? null : nearestRank(gaps, 0.5),
    p90: textOnly ? null : nearestRank(gaps, 0.9),
    kept: textOnly ? null : kept,
    bargeIns: textOnly ? null : stops.length,
    wer: !textOnly && call.wer_words ? call.wer_edits / call.wer_words : null,
    talk: textOnly ? null : call.talk_agent,
    durationMs: textOnly ? null : call.duration_ms,
    cost: call.cost_usd ?? null,
    endedReason: call.ended_reason ?? null,
    hasAudio: !textOnly && call.recording_status === "ok",
    recordingMissing: !textOnly && call.recording_status === "missing",
    failedTool: Boolean(call.failed_tool),
    latencySource: call.latency_source ?? null,
    callId: call.call_id ?? null,
  };
}

/**
 * Per-call stats for the grids (Matrix call cells, results voice columns):
 * `{calls: {[rowId]: {[runId]: callStats}}, personaOf: {[rowId]: key}}`,
 * limited to `runIds` when given.
 */
export function callTable(summary, { runIds } = {}) {
  const keptMs = summary?.thresholds?.kept_talking_ms;
  const calls = {};
  const personaOf = {};
  voiceRuns(summary)
    .filter((r) => !runIds || runIds.includes(r.run_id))
    .forEach((run) =>
      (run.calls ?? []).forEach((c) => {
        (calls[c.row_id] ??= {})[run.run_id] = callStats(c, { keptMs });
        personaOf[c.row_id] = c.persona;
      }),
    );
  return { calls, personaOf };
}

/** Run-level stats (Summary table, regression badges, verdict). */
export function voiceRunStats(run, summary) {
  const keptMs = summary?.thresholds?.kept_talking_ms ?? SECOND;
  const calls = run?.calls ?? [];
  const textOnly = isTextOnly(run);
  const completion = runRate(calls.map((c) => ({ trials: c.trials })));
  const gaps = calls.flatMap((c) => c.gaps ?? []);
  const ms = gaps.map((g) => g.ms);
  const stops = calls.flatMap((c) => c.barge_in_stops_ms ?? []);
  const words = calls.reduce((s, c) => s + (c.wer_words ?? 0), 0);
  const edits = calls.reduce((s, c) => s + (c.wer_edits ?? 0), 0);
  const costOf = (k) => mean(calls.map((c) => c.cost_usd?.[k]));
  const p99 = textOnly ? null : nearestRank(ms, 0.99);
  const slow = p99 == null ? [] : gaps.filter((g) => g.ms >= p99);
  return {
    runId: run.run_id,
    label: run.label,
    name: voiceName(run),
    fullName: run.name,
    textOnly,
    completion,
    p50: textOnly ? null : nearestRank(ms, 0.5),
    p90: textOnly ? null : nearestRank(ms, 0.9),
    p99,
    gaps: ms.length,
    // What the slowest turns have in common (`tool-call turns`).
    slowOnTools: slow.length > 0 && slow.every((g) => g.tool),
    stages: textOnly
      ? null
      : {
          stt: nearestRank(
            gaps.map((g) => g.stt),
            0.5,
          ),
          llm: nearestRank(
            gaps.map((g) => g.llm),
            0.5,
          ),
          tts: nearestRank(
            gaps.map((g) => g.tts),
            0.5,
          ),
        },
    kept: textOnly ? null : stops.filter((x) => x >= keptMs).length,
    bargeIns: textOnly ? null : stops.length,
    avgStopMs: textOnly || !stops.length ? null : mean(stops),
    wer: textOnly || !words ? null : edits / words,
    werParts: textOnly ? null : { words, edits },
    talk: textOnly
      ? null
      : nearestRank(
          calls.map((c) => c.talk_agent),
          0.5,
        ),
    costPerCall: mean(calls.map((c) => c.cost_usd?.total ?? 0)),
    costSplit: {
      stt: costOf("stt"),
      llm: costOf("llm"),
      tts: costOf("tts"),
      telephony: costOf("telephony"),
    },
    durationP50: textOnly
      ? null
      : nearestRank(
          calls.map((c) => c.duration_ms),
          0.5,
        ),
    sources: {
      recording: calls.filter((c) => c.latency_source === "recording").length,
      provider: calls.filter((c) => c.latency_source === "provider").length,
    },
    providerSource: run.provider_source ?? null,
  };
}

/** Latency source chip: `From LiveKit metrics · Preview` / `Reported by Vapi` / `Estimated from the recording`. */
export function sourceChipOf(stats) {
  if (!stats || stats.textOnly) return null;
  if (stats.sources.recording > 0)
    return { text: "Estimated from the recording", preview: true };
  if (stats.providerSource)
    return {
      text: stats.providerSource.text,
      preview: stats.providerSource.preview,
    };
  return { text: "Reported by the provider", preview: false };
}

/**
 * `Estimated from the recording (23) · Reported by the provider (1)`: the
 * calls of one run (`runId`) or of every voice run in the summary.
 */
export function sourcesText(summary, runId) {
  const run = runId
    ? (summary?.runs ?? []).find((r) => r.run_id === runId)
    : null;
  const s = run ? run.latency_sources ?? {} : summary?.sources ?? {};
  const parts = [];
  if (s.recording) parts.push(`Estimated from the recording (${s.recording})`);
  if (s.provider) parts.push(`Reported by the provider (${s.provider})`);
  return parts.join(" · ") || null;
}

/** The metrics a regression badge watches (direction: which way is worse). */
export const PINNED_METRICS = Object.freeze([
  {
    key: "completion",
    label: "Task completion",
    worse: "down",
    value: (s) => s.completion.rate,
    fmt: fmtPct,
  },
  {
    key: "p50",
    label: "Latency p50",
    worse: "up",
    value: (s) => s.p50,
    fmt: fmtLatency,
  },
  {
    key: "p90",
    label: "Latency p90",
    worse: "up",
    value: (s) => s.p90,
    fmt: fmtLatency,
  },
  {
    key: "p99",
    label: "Latency p99",
    worse: "up",
    value: (s) => s.p99,
    fmt: fmtLatency,
  },
  {
    key: "kept",
    label: "Kept talking over the caller",
    worse: "up",
    value: (s) => s.kept,
    fmt: (v) => fmtCount(v),
  },
  { key: "wer", label: "WER", worse: "up", value: (s) => s.wer, fmt: fmtPct },
]);

const WHOLE = { dp: 0 };
const relText = (rel) =>
  `${rel > 0 ? "+" : "−"}${fmtPct(Math.abs(rel), WHOLE)}`;

/**
 * Pinned metrics that moved more than REGRESSION_THRESHOLD the wrong way
 * (relative to the baseline). Text-only runs are compared on completion only.
 * `[{key, label, base, cand, rel, text}]`, `text` = `Latency p99 4.82 s vs 3.94 s (+22%)`.
 */
export function regressionsOf(baseStats, candStats) {
  if (!baseStats || !candStats) return [];
  return PINNED_METRICS.filter(
    (m) =>
      m.key === "completion" || (!baseStats.textOnly && !candStats.textOnly),
  )
    .map((m) => {
      const b = m.value(baseStats);
      const c = m.value(candStats);
      if (b == null || c == null) return null;
      const rel = b === 0 ? (c === 0 ? 0 : Infinity) : (c - b) / b;
      const wrong =
        m.worse === "up"
          ? rel > REGRESSION_THRESHOLD
          : rel < -REGRESSION_THRESHOLD;
      if (!wrong) return null;
      return {
        key: m.key,
        label: m.label,
        base: b,
        cand: c,
        rel,
        text: `${m.label} ${m.fmt(c)} vs ${m.fmt(b)}${Number.isFinite(rel) ? ` (${relText(rel)})` : ""}`,
      };
    })
    .filter(Boolean);
}

/** A metric label in a sentence: `Latency p99` → `latency p99`, `WER` stays. */
const inSentence = (label) =>
  String(label ?? "").replace(
    /^([A-Z])([a-z])/,
    (m, a, b) => a.toLowerCase() + b,
  );

/**
 * IT-R5 G6 P3: the badge names the metrics (`▼ Worse on latency p99, WER`;
 * from 3: `▼ Worse on latency p99, WER +1`), never a bare count. Null when
 * none.
 */
export function regressionBadgeText(regs) {
  if (!regs?.length) return null;
  const names = regs.map((r) => inSentence(r.label ?? r.key));
  const shown = names.slice(0, 2).join(", ");
  return `▼ Worse on ${shown}${names.length > 2 ? ` +${names.length - 2}` : ""}`;
}

/** Persona short label (`Mike · noisy line`). */
const personaShort = (summary, key) =>
  summary?.personas?.find((p) => p.key === key)?.short ?? key;

/**
 * Persona groups (scenarios datasets): rows per persona and each run's
 * completion on them (`Mike · noisy line  6/8 · 4/8 ▼`). `worse[runId]` when
 * the run completes fewer of the group's calls than the baseline.
 */
export function personaGroups(summary, { runIds, baselineId } = {}) {
  const matrix = voiceMatrix(summary);
  const ids = runIds ?? voiceRuns(summary).map((r) => r.run_id);
  const order = summary?.personas ?? [];
  const keys = [...new Set(matrix.rows.map((r) => r.persona))].sort(
    (a, b) =>
      order.findIndex((p) => p.key === a) - order.findIndex((p) => p.key === b),
  );
  return keys.map((key) => {
    const rows = matrix.rows.filter((r) => r.persona === key);
    const rowIds = rows.map((r) => r.id);
    const perRun = Object.fromEntries(
      ids.map((id) => [
        id,
        runRate(rows.map((r) => r.cells[id]).filter(Boolean)),
      ]),
    );
    const base = perRun[baselineId];
    const worse = Object.fromEntries(
      ids.map((id) => [
        id,
        id !== baselineId &&
          base?.rate != null &&
          perRun[id]?.rate != null &&
          perRun[id].rate < base.rate,
      ]),
    );
    return {
      key,
      label: personaShort(summary, key),
      rowIds,
      perRun,
      worse,
      text: ids
        .map((id) =>
          perRun[id]?.n ? `${perRun[id].k}/${perRun[id].n}` : EMPTY,
        )
        .join(" · "),
    };
  });
}

/**
 * The voice verdict of a candidate vs the baseline, from the calls:
 * `Acme voice v2 beats Acme Support v1: +12.5 pts task completion, p50
 * latency 0.86 s vs 1.42 s. 4 calls got worse (all on Mike · noisy line).`
 * Faster but completing fewer calls → tone `mixed`, `Faster, but completes
 * fewer calls: …` (never "beats"). Text-only vs voice → completion only.
 * @returns {{tone, verb, sentence, fasterButWorse, deltaPts, worse, better,
 *   scopeText, worseByPersona}|null}
 */
export function voiceVerdict(summary, { baselineId, candidateId } = {}) {
  const runs = voiceRuns(summary);
  const base = runs.find((r) => r.run_id === baselineId);
  const cand = runs.find((r) => r.run_id === candidateId);
  if (!base || !cand || base === cand) return null;
  const matrix = voiceMatrix(summary);
  const cmp = compareRuns({
    matrix,
    baselineId,
    candidateId,
    noun: "call",
    labels: { [baselineId]: base.label, [candidateId]: cand.label },
  });
  const bs = voiceRunStats(base, summary);
  const cs = voiceRunStats(cand, summary);
  const comparable = !bs.textOnly && !cs.textOnly;
  const delta = cmp.deltaPts ?? 0;
  const faster =
    comparable && cs.p50 != null && bs.p50 != null && cs.p50 < bs.p50;
  const slower =
    comparable && cs.p50 != null && bs.p50 != null && cs.p50 > bs.p50;
  const fasterButWorse = faster && delta < 0;
  const r3 = verdictOf({
    candidate: { label: cand.label },
    baseline: { label: base.label },
    metrics: {
      success: {
        cand: cmp.candidate.rate,
        base: cmp.baseline.rate,
        n: cmp.candidate.n,
        tries: 1,
        candN: cmp.candidate.n,
        baseN: cmp.baseline.n,
        deltaPts: delta,
      },
    },
  });
  const noise = VOICE_VERDICT_USES_SIGNIFICANCE && !r3.significant;
  let tone;
  let verb;
  if (cmp.partial) {
    tone = "tie";
    verb = "partial";
  } else if (fasterButWorse) {
    tone = "mixed";
    verb = "is faster but completes fewer calls than";
  } else if (noise) {
    tone = "tie";
    verb = "is level with";
  } else if (delta > 0) {
    tone = slower ? "mixed" : "better";
    verb = "beats";
  } else if (delta < 0) {
    tone = "worse";
    verb = "trails";
  } else {
    tone = "tie";
    verb = "is level with";
  }
  const byPersona = new Map();
  cmp.worseIds.forEach((id) => {
    const p = matrix.rows.find((r) => r.id === id)?.persona;
    byPersona.set(p, (byPersona.get(p) ?? 0) + 1);
  });
  const worseByPersona = [...byPersona.entries()].map(([key, n]) => ({
    key,
    label: personaShort(summary, key),
    n,
  }));
  const where =
    worseByPersona.length === 1
      ? ` (all on ${worseByPersona[0].label})`
      : worseByPersona.length > 1
        ? ` (${worseByPersona.map((w) => `${w.n} on ${w.label}`).join(", ")})`
        : "";
  const worseText = cmp.worse
    ? ` ${fmtCount(cmp.worse, "call")} got worse${where}.`
    : "";
  const latency = comparable
    ? `, reply latency p50 ${fmtLatency(cs.p50)} vs ${fmtLatency(bs.p50)}`
    : "";
  const body = `${fmtPts(delta)} task completion${noise && delta !== 0 ? " (within noise)" : ""}${latency}.`;
  let sentence;
  if (cmp.partial)
    sentence = `Partial · ${cmp.partial.scored} of ${fmtCount(cmp.partial.total, "try", "tries")} scored · verdict when done`;
  else if (fasterButWorse)
    sentence = `Faster, but completes fewer calls: ${cs.name} vs ${bs.name}, ${body}${worseText}`;
  else if (verb === "is level with")
    // IT-R5 G6 P2 (rule 37): `Too close to call`, never `is level with`.
    sentence = `Too close to call: ${cs.name} vs ${bs.name}, ${body}${worseText}`;
  else sentence = `${cs.name} ${verb} ${bs.name}: ${body}${worseText}`;
  // IT-R4 G6 P1 (S1): what the Matrix's `Level:` sentence needs — both
  // rates, the calls both ran, the 95% range of the difference (from the two
  // Wilson ranges) and the tail after the verdict.
  const nBoth = cmp.rows.filter((r) => r.change !== "not_run").length;
  // IT-R5 G6 P2 (R8, rule 36): ONE 95% range per pair — runNumbers'
  // diffRange95 on the calls both ran, as the Experiments headline.
  const level = {
    candRate: cmp.candidate.rate,
    baseRate: cmp.baseline.rate,
    n: nBoth,
    range: diffRange95(cmp.candidate.rate, cmp.baseline.rate, nBoth),
    tail: `${
      comparable
        ? ` Reply latency p50 ${fmtLatency(cs.p50)} vs ${fmtLatency(bs.p50)}.`
        : ""
    }${worseText}`,
  };
  return {
    tone,
    verb,
    sentence,
    level,
    fasterButWorse,
    comparable,
    deltaPts: delta,
    significant: r3.significant,
    confidence: r3.confidence,
    better: cmp.better,
    worse: cmp.worse,
    worseIds: cmp.worseIds,
    scopeText: cmp.scopeText,
    worseByPersona,
    base: bs,
    cand: cs,
  };
}

/** `Latency p99 is worse on Acme voice v2 (4.82 s vs 3.94 s): tool-call turns.` */
export function tailLine(bs, cs) {
  if (
    !bs ||
    !cs ||
    bs.textOnly ||
    cs.textOnly ||
    bs.p99 == null ||
    cs.p99 == null
  )
    return null;
  const rel = (cs.p99 - bs.p99) / bs.p99;
  if (rel <= REGRESSION_THRESHOLD) return null;
  return `Latency p99 is worse on ${cs.name} (${fmtLatency(cs.p99)} vs ${fmtLatency(bs.p99)})${cs.slowOnTools ? ": tool-call turns" : ""}.`;
}

/** `Acme voice v2 talks over callers less: 2 vs 11 times.` */
export function keptLine(bs, cs) {
  if (!bs || !cs || bs.kept == null || cs.kept == null || bs.kept === cs.kept)
    return null;
  const word = cs.kept < bs.kept ? "less" : "more";
  return `${cs.name} talks over callers ${word}: ${cs.kept} vs ${fmtCount(bs.kept, "time")}.`;
}

const STAGES = [
  ["stt", "STT"],
  ["llm", "LLM"],
  ["tts", "TTS"],
];
/** `LLM is the slowest stage on both runs · 0.49 s vs 0.74 s p50`. */
export function stageLine(statsList) {
  const list = (statsList ?? []).filter((s) => s?.stages);
  if (!list.length) return null;
  const slowest = (s) =>
    STAGES.reduce(
      (best, [k]) => (s.stages[k] > (s.stages[best] ?? -1) ? k : best),
      "stt",
    );
  const keys = list.map(slowest);
  const name = STAGES.find(([k]) => k === keys[0])[1];
  if (keys.every((k) => k === keys[0])) {
    const all =
      list.length > 2
        ? "every run"
        : list.length === 2
          ? "both runs"
          : "this run";
    return `${name} is the slowest stage on ${all} · ${list.map((s) => fmtLatency(s.stages[keys[0]])).join(" vs ")} p50`;
  }
  return list
    .map(
      (s, i) =>
        `${STAGES.find(([k]) => k === keys[i])[1]} is the slowest stage on ${s.label}`,
    )
    .join(" · ");
}

/** Cost per call with its split (tooltip `STT $0.01 · LLM … · telephony …`). */
export function costSplitText(stats) {
  const sp = stats?.costSplit ?? {};
  return [
    ["STT", sp.stt],
    ["LLM", sp.llm],
    ["TTS", sp.tts],
    ["Telephony", sp.telephony],
  ]
    .filter(([, v]) => v != null)
    .map(([k, v]) => `${k} ${fmtCost(v)}`)
    .join(" · ");
}

/** Every Summary line in order: verdict, tail, interruptions. */
export function voiceVerdictList(summary, { baselineId, candidateIds } = {}) {
  const out = [];
  (candidateIds ?? []).forEach((id) => {
    const v = voiceVerdict(summary, { baselineId, candidateId: id });
    if (!v) return;
    out.push({
      key: `verdict:${id}`,
      tone: v.tone,
      text: v.sentence,
      verdict: v,
    });
    if (!v.comparable) return;
    const tail = tailLine(v.base, v.cand);
    if (tail) out.push({ key: `tail:${id}`, tone: "worse", text: tail });
    const kept = keptLine(v.base, v.cand);
    if (kept)
      out.push({
        key: `kept:${id}`,
        tone: v.cand.kept < v.base.kept ? "better" : "worse",
        text: kept,
      });
  });
  return out;
}

/** Latency heat (sequential): 0 at ≤ 1.0 s, 1 at ≥ 3.0 s. */
export const HEAT_STOPS_MS = Object.freeze([SECOND, 2 * SECOND, 3 * SECOND]);
export function latencyHeat(ms) {
  if (ms == null) return null;
  const [lo, , hi] = HEAT_STOPS_MS;
  return Math.max(0, Math.min(1, (ms - lo) / (hi - lo)));
}
/** `≤ 1.0 s · 2.0 s · ≥ 3.0 s`. */
export const heatLegend = () =>
  HEAT_STOPS_MS.map(
    (ms, i) =>
      `${i === 0 ? "≤ " : i === HEAT_STOPS_MS.length - 1 ? "≥ " : ""}${fmtMs(ms)}`,
  );

/** Calls per row and run (`{[rowId]: {[runId]: call}}`) for the grids. */
export function callsByRow(summary) {
  const out = {};
  voiceRuns(summary).forEach((run) =>
    (run.calls ?? []).forEach((c) => {
      (out[c.row_id] ??= {})[run.run_id] = c;
    }),
  );
  return out;
}
