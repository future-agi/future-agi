// Pure helpers behind MediaValue: persona values, call chip
// text, image fitting. No React. Every number goes through the one
// formatter; templates carry no digit literals (§C10).
import { fmtMs } from "src/sections/common/format/fmt";
import { callOf, conversationOf, fmtDuration, mediaRefOf } from "./mediaRef";

const parse = (v) => {
  if (v && typeof v === "object") return v;
  if (typeof v !== "string") return null;
  const t = v.trim();
  if (!t.startsWith("{")) return null;
  try {
    return JSON.parse(t);
  } catch {
    return null;
  }
};

/**
 * A persona value (`{name, label?, traits?}`, object or JSON text) →
 * `{name, label, traits: [[key, value]], initial}`, else null. Only small
 * objects whose keys are persona keys count (a row with `name` and twenty
 * other fields is not a persona).
 */
export function personaOf(value) {
  const v = parse(value);
  if (!v || Array.isArray(v) || typeof v.name !== "string" || !v.name.trim())
    return null;
  const allowed = new Set([
    "name",
    "label",
    "traits",
    "description",
    "id",
    "persona_id",
    "age",
    "language",
  ]);
  if (!Object.keys(v).every((k) => allowed.has(k))) return null;
  if (
    !(typeof v.label === "string" || (v.traits && typeof v.traits === "object"))
  )
    return null;
  const traits =
    v.traits && typeof v.traits === "object" && !Array.isArray(v.traits)
      ? Object.entries(v.traits).filter(
          ([, x]) => x != null && typeof x !== "object",
        )
      : [];
  const label = typeof v.label === "string" && v.label ? v.label : v.name;
  return {
    name: v.name,
    label,
    traits,
    description: typeof v.description === "string" ? v.description : null,
    initial: v.name.trim()[0].toUpperCase(),
  };
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/**
 * Call chip text: `1 m 48 s · p50 900 ms · 1 interruption`; text-only
 * `Text only · 9 turns`; compact (72 px Matrix cells) `900 ms · ⚡1`.
 */
export function callChipText(call, { compact = false } = {}) {
  if (!call) return "";
  const turns = call.turns?.length;
  if (call.depth === "text_only")
    return compact
      ? "Text only"
      : `Text only${Number.isFinite(turns) ? ` · ${plural(turns, "turn", "turns")}` : ""}`;
  const p50 = call.metrics?.latency_ms?.p50;
  const kept = call.metrics?.interruptions?.agent_kept_talking ?? 0;
  if (compact)
    return [Number.isFinite(p50) ? fmtMs(p50) : "—", kept ? `⚡${kept}` : null]
      .filter(Boolean)
      .join(" · ");
  const parts = [fmtDuration(call.duration_ms)];
  if (Number.isFinite(p50)) parts.push(`p50 ${fmtMs(p50)}`);
  parts.push(plural(kept, "interruption", "interruptions"));
  return parts.join(" · ");
}

/** Tooltip lines for a call chip. */
export function callChipTooltip(call) {
  if (!call) return "";
  const lines = [
    `${call.provider ? `${call.provider} · ` : ""}${call.depth === "text_only" ? "Text only" : call.depth === "replay" ? "Replay" : "Full voice"} · ended: ${call.ended_reason ?? call.status}`,
  ];
  if (call.depth !== "text_only") {
    const m = call.metrics ?? {};
    if (Number.isFinite(m.latency_ms?.p90))
      lines.push(
        `Latency p50 ${fmtMs(m.latency_ms.p50)} · p90 ${fmtMs(m.latency_ms.p90)} (${call.latency_source === "provider" ? "reported by the provider" : "estimated from the recording"})`,
      );
    const kept = m.interruptions?.agent_kept_talking;
    const barge = m.interruptions?.caller_barge_ins;
    if (Number.isFinite(barge))
      lines.push(
        `Kept talking over the caller ${kept ?? 0} of ${barge} barge-ins`,
      );
    if (!call.recording) lines.push("Recording missing");
  }
  return lines.join("\n");
}

/** Never upscale past 2×: the displayed size of an image in a box. */
export function fittedSize(w, h, maxW, maxH, { maxUpscale = 2 } = {}) {
  if (!w || !h) return { w: maxW, h: maxH, scale: 1 };
  const scale = Math.min(maxW / w, maxH / h, maxUpscale);
  return { w: Math.round(w * scale), h: Math.round(h * scale), scale };
}

/** `0:04` clock for audio cells (under an hour). */
export function clockOf(ms) {
  const n = Number(ms);
  if (!Number.isFinite(n) || n < 0) return "0:00";
  const s = Math.round(n / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/**
 * The 1-based failing turn of a conversation value: `failing_turn` as the
 * data states it, else the index of the message flagged failing + 1.
 */
export function failingTurnOf(value, conv) {
  if (!conv || !Number.isInteger(conv.failingTurn)) return null;
  const v = parse(value);
  return v && Number.isInteger(v.failing_turn)
    ? v.failing_turn
    : conv.failingTurn + 1;
}

// Column data types (DataTypes in DevelopCellRenderer) → mediaRefOf's
// column type; text / json / persona columns sniff the value.
const COLUMN_MEDIA_TYPE = {
  image: "image",
  images: "images",
  audio: "audio",
  video: "video",
  document: "document",
};
export const columnMediaType = (dataType) => COLUMN_MEDIA_TYPE[dataType];

const isEmptyCell = (v) =>
  v == null ||
  v === "" ||
  (Array.isArray(v) && !v.length) ||
  (typeof v === "string" && ["[]", "null"].includes(v.trim()));

/**
 * How a grid / row-view value renders: `call` · `persona` · `media` · null
 * (render as before). A conversation (even one with an image part) is a
 * chat, never its attachment; empty media cells keep their old renderer
 * (the upload prompt); a text cell holding a bare file name stays text.
 * @param {*} value
 * @param {string} [dataType]  the column's data type
 */
export function mediaCellKind(value, dataType) {
  if (isEmptyCell(value)) return null;
  if (callOf(value)) return "call";
  if (conversationOf(value)) return null;
  if (
    ["text", "json", "persona", undefined].includes(dataType) &&
    personaOf(value)
  )
    return "persona";
  const columnType = COLUMN_MEDIA_TYPE[dataType];
  const ref = mediaRefOf(value, { columnType });
  if (!ref) return null;
  const list = Array.isArray(ref) ? ref : [ref];
  if (
    !columnType &&
    !list.every((r) => /^(https?:|data:|blob:|\/)/i.test(r.url ?? ""))
  )
    return null;
  return "media";
}
