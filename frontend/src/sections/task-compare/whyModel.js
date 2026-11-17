// Side by side · the mechanical "why they differ" line (pure). Built from
// G0 markers() and the failing check's evidence, never from AI prose:
// `Why they differ: #2B read the order id as "8830"; #2A and #2D read "A-8830".`
// ≤ 35 words. Tested in __tests__/whyModel.test.js.

import { marksOfLane } from "src/sections/common/Trajectory/trajectoryMarkers";
import {
  callMetricsOf,
  rankedCallDifference,
} from "src/sections/common/Lenses/lensModel";

const parse = (v) => {
  if (typeof v !== "string") return v;
  try {
    return JSON.parse(v);
  } catch {
    return v;
  }
};

/** The order id a step read or used (`order_ref` / `order_id`), else null. */
export function orderIdOf(step) {
  if (!step) return null;
  const out = parse(step.output);
  const inp = parse(step.input);
  const pick = (o) =>
    o && typeof o === "object" && !Array.isArray(o)
      ? o.order_ref ?? o.order_id ?? null
      : null;
  if (step.kind === "think") return pick(out);
  return pick(inp) ?? pick(out);
}

const joinLabels = (labels) =>
  labels.length <= 1
    ? labels[0] ?? ""
    : `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`;

function cellSteps(cell) {
  if (!cell) return [];
  if (cell.type === "gap") return cell.steps ?? [];
  return [...(cell.subSteps ?? []), cell.step].filter(Boolean);
}

const clampWords = (s, max = 35) => {
  const w = s.split(/\s+/);
  return w.length <= max ? s : `${w.slice(0, max).join(" ")}…`;
};

/**
 * @param {{rows, lanes: [{id, label, steps, checks, verdict?}], markers:
 *   {firstWrongStep, firstDifference}}} args  lanes base first
 * @returns {string|null} the sentence (null when nothing differs)
 */
export function whySentence({ rows = [], lanes = [], markers = {} } = {}) {
  const fw = markers.firstWrongStep;
  if (fw) {
    const li = lanes.findIndex((l) => l.id === fw.laneId);
    const lane = lanes[li];
    const step = lane?.steps?.find((s) => s.n === fw.stepN);
    const row = rows.find((r) => r.id === fw.rowId);
    const id = orderIdOf(step);
    if (id && row) {
      // What the other lanes read at the same aligned row.
      const groups = new Map();
      lanes.forEach((l, i) => {
        if (i === li) return;
        const steps = cellSteps(row.cells[i]);
        const theirs = steps.map(orderIdOf).find(Boolean);
        if (!theirs || theirs === id) return;
        if (!groups.has(theirs)) groups.set(theirs, []);
        groups.get(theirs).push(l.label);
      });
      const others = [...groups.entries()]
        .map(([v, labels]) => `${joinLabels(labels)} read "${v}"`)
        .join("; ");
      return clampWords(
        `Why they differ: ${lane.label} read the order id as "${id}"${others ? `; ${others}` : ""}.`,
      );
    }
    const failed = (lane?.checks ?? []).find((c) => c.passed === false);
    const passing = lanes
      .filter((l, i) => i !== li && l.verdict?.key === "pass")
      .map((l) => l.label);
    const tail = passing.length
      ? `; ${joinLabels(passing)} passed every trial`
      : "";
    if (failed?.evidence)
      return clampWords(
        `Why they differ: ${lane.label} failed ${failed.key} (${failed.evidence})${tail}.`,
      );
    if (failed)
      return clampWords(
        `Why they differ: ${lane.label} failed ${failed.key}${tail}.`,
      );
  }
  const fd = markers.firstDifference;
  if (fd?.text) return clampWords(`Why they differ: ${fd.text}.`);
  return null;
}

/**
 * `First wrong step · step 1 · read the order id as "7781"` for one lane
 * alone (G0 milestones via marksOfLane; never our own step numbers), or
 * null when the lane has no first wrong step.
 * @param {{id: string, label?: string, steps: object[], checks?: object[]}} lane
 */
export function firstWrongLineOf(lane) {
  const m = marksOfLane({
    id: lane?.id,
    label: lane?.label,
    steps: lane?.steps ?? [],
    checks: lane?.checks ?? [],
  });
  const n = m.firstWrongStepN;
  if (!Number.isInteger(n)) return null;
  const step = (lane.steps ?? []).find((s) => s.n === n);
  const id = orderIdOf(step);
  const failed = (lane.checks ?? []).find((c) => c.passed === false);
  const why = id
    ? `read the order id as "${id}"`
    : failed?.evidence ??
      (failed ? `failed ${failed.name ?? failed.key}` : null);
  return `First wrong step · step ${n}${why ? ` · ${why}` : ""}`;
}

/**
 * IT-R1 G4 · the Why line of a call row, from the calls' own metrics (one
 * number source: lensModel.callMetricsOf + rankedCallDifference, fmtMs):
 * `Acme voice v2 finished the task, Acme Support v1 did not · Acme voice v2
 * talked over the caller 2× less · Acme voice v2 replied in 820 ms vs 1.1 s
 * (p50) · first
 * difference: turn 9 · …`. Baseline vs the first other lane. Null without
 * two calls.
 * @param {{lanes: {id, verdict}[], calls: (object|null)[], names: Map,
 *   fmtMs: (ms) => string}} args
 */
export function voiceWhy({ lanes = [], calls = [], names, fmtMs } = {}) {
  if (lanes.length < 2 || !calls[0] || !calls[1]) return null;
  const nameOf = (l) => names?.get?.(l.id) ?? l.label ?? "A run";
  const [A, B] = [nameOf(lanes[0]), nameOf(lanes[1])];
  const done = (l) =>
    l.verdict?.scored ? l.verdict.pass === l.verdict.scored : null;
  const [da, db] = [done(lanes[0]), done(lanes[1])];
  const parts = [];
  if (da === true && db === false)
    parts.push(`${A} finished the task, ${B} did not`);
  else if (da === false && db === true)
    parts.push(`${B} finished the task, ${A} did not`);
  else if (da === true && db === true) parts.push("Both finished the task");
  else if (da === false && db === false)
    parts.push("Neither finished the task");
  const [ma, mb] = [callMetricsOf(calls[0]), callMetricsOf(calls[1])];
  if (ma.kept != null && mb.kept != null && ma.kept !== mb.kept) {
    const [lo, hi, nLo, nHi] =
      mb.kept < ma.kept ? [B, A, mb.kept, ma.kept] : [A, B, ma.kept, mb.kept];
    parts.push(
      nLo === 0
        ? `${lo} never talked over the caller (${hi} did ${nHi}×)`
        : `${lo} talked over the caller ${Math.round((nHi / nLo) * 10) / 10}× less`,
    );
  }
  if (ma.p50 != null && mb.p50 != null && fmtMs)
    parts.push(`${B} replied in ${fmtMs(mb.p50)} vs ${fmtMs(ma.p50)} (p50)`);
  const diff = rankedCallDifference(calls.slice(0, 2), { names: [A, B] });
  if (diff)
    parts.push(
      `first difference: turn ${diff.turn} · ${diff.short ?? diff.text.replace(/^First difference: turn \d+ · /, "")}`,
    );
  if (!parts.length) return null;
  const s = parts.join(" · ");
  return `${s.charAt(0).toUpperCase()}${s.slice(1)}.`;
}

// Self-hosted voice connectors (dataset integration P4b) and model vendors.
const PROVIDER_NAME = {
  websocket: "WebSocket",
  webrtc: "WebRTC",
  livekit: "LiveKit",
  pipecat: "Pipecat",
  deepgram: "Deepgram",
  elevenlabs: "ElevenLabs",
  cartesia: "Cartesia",
  openai: "OpenAI",
};
const titled = (v) =>
  v ? PROVIDER_NAME[String(v).toLowerCase()] ?? String(v) : null;

/**
 * IT-R1 G4 · what changed between two voice runs, from their configs:
 * `WebSocket → LiveKit · gpt-4o-mini → gpt-4.1-mini · STT nova-2 → nova-3 · TTS
 * ElevenLabs Rachel → Cartesia Sarah`. Null when nothing differs or a
 * config is missing. Pure.
 * @param {object} a  baseline run config (`config.voice`)
 * @param {object} b  the other run's config
 */
export function voiceChangeLine(a, b) {
  const va = a?.voice;
  const vb = b?.voice;
  if (!va || !vb) return null;
  const parts = [];
  const pair = (x, y, prefix = "") => {
    if (x && y && x !== y) parts.push(`${prefix}${x} → ${y}`);
  };
  pair(titled(va.provider), titled(vb.provider));
  pair(va.llm?.model, vb.llm?.model);
  pair(va.stt?.model, vb.stt?.model, "STT ");
  const tts = (v) =>
    [titled(v.tts?.provider), v.tts?.voice].filter(Boolean).join(" ") || null;
  pair(tts(va), tts(vb), "TTS ");
  pair(va.turn_detection, vb.turn_detection, "turn detection ");
  return parts.length ? parts.join(" · ") : null;
}

/**
 * IT-R5 G4 P2: the why of a media row (TTS clips, images) from the lanes'
 * own scores: `Cartesia sonic-2 Sarah round-trip WER 10.0% (passes at
 * ≤ 5%).` (summarySentence reads it as `… Sarah failed: round-trip …`). A check named `<label> ≤ <rule>` / `<label> ≥ <rule>` reads
 * as `<label> <measured> (passes at ≤ <rule>)`. Null when no lane failed a
 * check. Pure.
 * @param {{lanes: {id, label?, trial?: {scores?: object}}[], names?: Map}} a
 */
export function scoreWhy({ lanes = [], names } = {}) {
  const nameOf = (l) => names?.get?.(l.id) ?? l.label ?? "A run";
  const failedOf = (l) =>
    Object.values(l.trial?.scores ?? {}).filter(
      (sc) => sc && sc.status === "failed" && sc.name,
    );
  const bad = lanes.filter((l) => failedOf(l).length);
  if (!bad.length) return null;
  const clause = (sc) => {
    const m = /^(.*?)\s*([≤≥])\s*(.+)$/.exec(sc.name);
    if (!m) return `${sc.name} ${sc.display ?? "failed"}`;
    const label = m[1].charAt(0).toLowerCase() + m[1].slice(1);
    return `${label} ${sc.display ?? ""} (passes at ${m[2]} ${m[3]})`.replace(
      /\s+/g,
      " ",
    );
  };
  // `<run> <clause>`: the summary sentence attaches the clause to that
  // run's `failed` (`… Sarah failed: round-trip WER 10.0% (passes at ≤ 5%)`).
  const parts = bad.map(
    (l) => `${nameOf(l)} ${failedOf(l).map(clause).join(", ")}`,
  );
  return `${parts.join("; ")}.`;
}
