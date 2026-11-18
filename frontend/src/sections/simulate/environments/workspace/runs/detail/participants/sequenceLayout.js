import { useLayoutEffect, useState } from "react";
import { alpha } from "@mui/material/styles";
import { typography } from "src/theme/typography";

export const GUT = 0;
export const LANE_PAD = 80; // half a lane header, plus air
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

export const TAG_W = (k) => (k ? k.length * 8 + 6 : 0);

export const smallBtn = {
  height: 24,
  px: 1,
  typography: "s3",
  whiteSpace: "nowrap",
};

// Text printed from a message object keeps its line breaks escaped; show them as breaks.
export const readable = (text) => {
  const s = String(text ?? "");
  return s.includes("\\n") && !s.includes("\n")
    ? s.replaceAll("\\n", "\n").replaceAll("\\'", "'")
    : s;
};

// A text the API marks as data or prose, as the markdown that shows it.
export const asMarkdown = (shown) => {
  if (shown.format !== "code") return shown.text;
  const longest = Math.max(
    0,
    ...(shown.text.match(/`+/g) ?? []).map((run) => run.length),
  );
  const fence = "`".repeat(Math.max(3, longest + 1));
  return `${fence}\n${shown.text}\n${fence}`;
};

// The theme's type, for SVG text: s3 for small words, s1 for names and step labels.
export const SVG_TYPE = {
  sm: typography.s3.fontSize,
  md: typography.s1.fontSize,
  mono: typography.code_sm.fontFamily,
  family: typography.fontFamily,
};

// The theme's colours the drawing uses.
export const coloursOf = (theme) => ({
  fg: theme.palette.text.primary,
  sub: theme.palette.text.secondary,
  muted: theme.palette.text.tertiary || theme.palette.text.secondary,
  faint: theme.palette.text.disabled,
  line: theme.palette.divider,
  code: theme.palette.background.neutral,
  paper: theme.palette.background.paper,
  bad: theme.palette.error.main,
  warn: theme.palette.warning.main,
  sel: alpha(theme.palette.primary.main, 0.08),
  selLine: theme.palette.primary.main,
  hover: theme.palette.action.hover,
  onColour: theme.palette.common.white,
});

/* Where everything sits for a width: lane x positions, lane heads, and each
   row on show (in order) with its centre line y. */
export const layoutOf = (lanes, width, ordered) => {
  const L = lanes.length;
  const laneW = Math.max(240, width - GUT);
  const gap = Math.max(
    MIN_GAP,
    Math.min(230, (laneW - 2 * LANE_PAD) / Math.max(1, L - 1)),
  );
  const X = Object.fromEntries(
    lanes.map((ln, i) => [ln.id, LANE_PAD + i * gap]),
  );
  /* Never wider than the first lane's padding, or it clips at the edge. */
  const headW = Math.min(150, gap - 8, 2 * LANE_PAD - 6);
  const nameChars = Math.floor((headW - 12) / 6.6);
  const heads = lanes.map((ln) => nameLines(ln.name, nameChars));
  const headH = heads.some((h) => h.length > 1) ? 54 : 40;
  const svgW = Math.max(laneW, 2 * LANE_PAD + gap * (L - 1));
  let y = 10 + headH + 42; // first row's centre line
  const rows = ordered.map((r) => {
    const out = { ...r, y };
    y += ROW_H[r.type];
    return out;
  });
  return {
    X,
    headW,
    heads,
    headH,
    svgW,
    rows,
    H: y + 6,
    /* A tool call is a row of its own, so its one line may run across the
       lanes beside it (on a paper backing); left of the lane when the lane is
       near the right edge. */
    toolSide: (from) => {
      const right = svgW - X[from] - 30;
      const left = X[from] - 30;
      return right >= Math.min(260, left)
        ? { left: false, room: right }
        : { left: true, room: left };
    },
    /* A label may run past a short arrow; it stays inside the drawing. */
    roomOf: (x1, x2) => Math.floor(Math.max(Math.abs(x2 - x1) - 16, 210) / CH),
    placeX: (mid, text) => {
      const half = (text.length * CH) / 2 + 4;
      return Math.min(svgW - half, Math.max(half, mid));
    },
  };
};
