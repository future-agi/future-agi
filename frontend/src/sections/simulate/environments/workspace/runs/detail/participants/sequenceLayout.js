import { useLayoutEffect, useState } from "react";

export const GUT = 0;
export const LANE_PAD = 64; // half a lane header, plus air
export const MIN_GAP = 118; // lanes closer than this scroll sideways
export const ROW_H = {
  go: 44,
  tool: 34,
  back: 36,
  none: 38,
  took: 38,
  turn: 34,
};
// A call longer than this groups by the caller's turns.
export const LONG_STEPS = 12;
export const CH = 6.4; // average character width at 12px
/* A step says how long it took only when that's part of what happened. */
export const SLOW_SEC = 20;

export const cut = (text, chars) => {
  const s = String(text || "");
  if (chars < 4) return "";
  return s.length <= chars ? s : `${s.slice(0, chars - 1).trimEnd()}…`;
};
export const KIND = {
  message: "Message",
  handoff: "Hand-off",
  tool: "Tool call",
};

/** 0.4 s · 3.2 s · 42 s · 1 min 13 s */
export const took = (sec) => {
  if (sec == null || Number.isNaN(sec)) return "—";
  if (sec < 10) return `${sec.toFixed(1)} s`;
  if (sec < 60) return `${Math.round(sec)} s`;
  const m = Math.floor(sec / 60);
  const r = Math.round(sec % 60);
  return r ? `${m} min ${r} s` : `${m} min`;
};

export function useWidth(ref) {
  const [w, setW] = useState(820);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(([e]) =>
      setW(Math.round(e.contentRect.width)),
    );
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

/* Lane header: the name on one line, or two when it doesn't fit. */
export const nameLines = (name, chars) => {
  const s = String(name || "");
  if (s.length <= chars) return [s];
  const words = s.split(" ");
  let first = "";
  while (words.length && `${first} ${words[0]}`.trim().length <= chars)
    first = `${first} ${words.shift()}`.trim();
  if (!first) return [cut(s, chars)];
  return [first, cut(words.join(" "), chars)];
};

/**
 * The diagram. `selected` is a step number; picking a step (its arrow, its
 * answer, its pill) calls onSelect(n). The step column stays put while the
 * lanes scroll sideways in a narrow drawer.
 */

// What an arrow's words are: words with the caller, a task handed over, a result sent back, or a tool call.
export const kindOf = (r, s) => {
  if (r.type === "tool") return "TOOL";
  if (r.type === "took") return null;
  if (r.type === "go") return s.kind === "message" ? "SAID" : "TASK";
  return s.kind === "message" ? "SAID" : "RESULT";
};

export const KIND_WORDS = {
  SAID: "Said",
  TASK: "Task handed over",
  RESULT: "Result sent back",
  TOOL: "Tool call",
};

export const TAG_W = (k) => (k ? k.length * 6.2 + 6 : 0);

export const json = (v) =>
  v == null ? null : typeof v === "string" ? v : JSON.stringify(v, null, 2);

export const smallBtn = {
  height: 24,
  px: 1,
  typography: "s3",
  whiteSpace: "nowrap",
};
