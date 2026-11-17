// W5-8 Find (DE-11; W5-SCOPE §7 C4): the pure half of "Find in this result".
// The DOM half (TreeWalker over text nodes, CSS highlights, scrolling) is the
// one find engine the trace drawer already has,
// components/traceDetail/useSearchHighlight.js; useFindInPanel.js adds the
// Cmd/Ctrl+F door and the bar around it.

/** Cmd+F (macOS) or Ctrl+F (elsewhere), without Shift or Alt. */
export function isFindShortcut(e) {
  if (!e || e.altKey || e.shiftKey) return false;
  const key = String(e.key ?? "").toLowerCase();
  return key === "f" && Boolean(e.metaKey || e.ctrlKey);
}

/** An element that takes typing (an input, a text area, contenteditable). */
function isEditable(el) {
  if (!el || typeof el !== "object") return false;
  const tag = String(el.tagName ?? "").toUpperCase();
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") {
    const type = String(el.type ?? "text").toLowerCase();
    return !["button", "checkbox", "radio", "submit", "reset"].includes(type);
  }
  return el.isContentEditable === true;
}

/**
 * True when a Cmd/Ctrl+F belongs to the panel: focus (or the event target)
 * is inside it, or the last click landed inside it and focus is not in a
 * place that takes typing outside it (clicking text focuses nothing, so a
 * button clicked before, such as the one that opened the panel, keeps
 * focus). Outside the panel the browser's own find runs as usual.
 */
export function ownsFindShortcut(
  e,
  container,
  activeElement,
  lastPointerInside = false,
) {
  if (!isFindShortcut(e) || !container) return false;
  const inside = (node) =>
    Boolean(
      node &&
        typeof container.contains === "function" &&
        container.contains(node),
    );
  if (inside(e.target) || inside(activeElement)) return true;
  return Boolean(
    lastPointerInside &&
      !isEditable(activeElement) &&
      container.isConnected !== false,
  );
}

/** `3 of 7`; `No matches` for a query with none; "" before typing. */
export function countLabel(activeIndex, count, query) {
  if (!String(query ?? "").trim()) return "";
  if (!count) return "No matches";
  const i = Math.min(Math.max(0, activeIndex), count - 1);
  return `${(i + 1).toLocaleString()} of ${count.toLocaleString()}`;
}

/**
 * How many times `query` occurs in `text`, case-insensitive and without
 * overlaps (the highlight engine's rule: the next search starts after the
 * previous match).
 */
export function countOccurrences(text, query) {
  const q = String(query ?? "")
    .trim()
    .toLowerCase();
  if (!q) return 0;
  const t = String(text ?? "").toLowerCase();
  let n = 0;
  let i = t.indexOf(q);
  while (i !== -1) {
    n += 1;
    i = t.indexOf(q, i + q.length);
  }
  return n;
}

/** A double-quoted expression string literal (`"` and `\` escaped). */
export function quoteLiteral(text) {
  return `"${String(text ?? "")
    .replace(/\\/g, "\\\\")
    .replace(/"/g, '\\"')}"`;
}

const IDENT = /^[A-Za-z_][A-Za-z0-9_.]*$/;

/** A field name as the expression language spells it (back-quoted when needed). */
export function fieldRef(name) {
  const n = String(name ?? "");
  return IDENT.test(n) ? n : `\`${n.replace(/`/g, "")}\``;
}

/**
 * "Filter rows with this": `output contains "<text>" or <primary input>
 * contains "<text>"` (W5-SCOPE §7 C4). Without a primary input only the
 * output clause.
 */
export function filterExpressionFor(text, primaryInput) {
  const lit = quoteLiteral(String(text ?? "").trim());
  const out = `output contains ${lit}`;
  return primaryInput
    ? `${out} or ${fieldRef(primaryInput)} contains ${lit}`
    : out;
}
