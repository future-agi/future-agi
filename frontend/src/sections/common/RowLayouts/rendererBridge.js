// Row layouts (W9-4, B4): the parent side of a custom renderer. It accepts
// messages only from the renderer's own window (the frame inside our
// wrapper frame, sandboxDoc.js) and only in the known shapes (from the
// wrapper's window only its report of a refused frame navigation);
// a proposed change must name an existing, editable user column and respect
// the rate limits, and then becomes one pending update_cell with the layout
// as its source. The row payload carries values only. Pure (no React).
import { clampHeight } from "./layoutModel";
import { MSG } from "./sandboxDoc";

/** W9-SCOPE §6 B4: one proposal per 500 ms, 50 per Row-view session. */
export const PROPOSE_LIMITS = Object.freeze({
  intervalMs: 500,
  perSession: 50,
});

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const quote = (s) => `“${s}”`;

/** What the renderer sees of the row: values by column name, per role. */
export function rowPayload(model, row, rowId) {
  const byName = (fields = []) =>
    Object.fromEntries(fields.map((f) => [f.column.name, f.value ?? null]));
  const number = Number.isFinite(row?.order) ? row.order + 1 : null;
  return {
    id: rowId ?? row?.row_id ?? null,
    number,
    input: byName(model?.input),
    expected: byName(model?.expected),
    metadata: byName(model?.metadata),
    other: byName(model?.noRole),
    outputs: Object.fromEntries(
      (model?.outputs ?? []).map((o) => [
        o.column.name,
        o.cell?.cell_value ?? null,
      ]),
    ),
  };
}

/** A value a cell can hold: text, number, true/false or null; objects as JSON. */
function cellValueOf(v) {
  if (v == null || ["string", "number", "boolean"].includes(typeof v))
    return v ?? null;
  try {
    return JSON.stringify(v);
  } catch {
    return undefined;
  }
}

/** Known message shapes; anything else is ignored. */
export function parseMessage(data) {
  if (!isObj(data) || typeof data.type !== "string") return null;
  switch (data.type) {
    case MSG.ready:
      return { type: MSG.ready };
    case MSG.csp:
      return typeof data.directive === "string" &&
        typeof data.blocked === "string"
        ? { type: MSG.csp, directive: data.directive, blocked: data.blocked }
        : null;
    case MSG.height:
      // `auto`: the frame following its content (capped by the layout's
      // max height); otherwise the author's fa.setHeight.
      return Number.isFinite(data.px)
        ? {
            type: MSG.height,
            px: clampHeight(data.px),
            ...(data.auto === true ? { auto: true } : {}),
          }
        : null;
    case MSG.propose: {
      if (typeof data.column !== "string") return null;
      const value = cellValueOf(data.value);
      return value === undefined
        ? null
        : { type: MSG.propose, column: data.column, value };
    }
    case MSG.error:
      return typeof data.message === "string"
        ? { type: MSG.error, message: data.message.slice(0, 300) }
        : null;
    default:
      return null;
  }
}

/**
 * The frame's height for a height message: content-fit heights stop at the
 * layout's max height (`renderer.height`); fa.setHeight is taken as asked.
 */
export function frameHeight(msg, maxHeight) {
  const cap = Number(maxHeight);
  return msg.auto && Number.isFinite(cap) && cap > 0
    ? Math.min(msg.px, clampHeight(cap))
    : msg.px;
}

/** The panel line for a blocked request. */
export function blockedLine(blocked, directive) {
  const what =
    blocked && blocked !== "inline" && blocked !== "eval" ? blocked : directive;
  return `Blocked: the renderer tried to reach ${what} (renderers have no network).`;
}

/** What the wrapper's policy refuses: a frame navigating. */
const WRAPPER_DIRECTIVES = Object.freeze(["frame-src", "child-src"]);

/**
 * A message from the wrapper document (our code, no author code): only its
 * report of a refused frame navigation, else null.
 */
export function acceptWrapper(event, wrapperWindow) {
  if (!wrapperWindow || event?.source !== wrapperWindow) return null;
  const msg = parseMessage(event.data);
  return msg?.type === MSG.csp && WRAPPER_DIRECTIVES.includes(msg.directive)
    ? msg
    : null;
}

/**
 * The renderer's own window: the frame inside the wrapper document that the
 * app's iframe holds. Cross-origin frames still expose their child frames.
 */
export function rendererWindow(frameEl) {
  try {
    return frameEl?.contentWindow?.[0] ?? null;
  } catch {
    return null;
  }
}

/**
 * One renderer session (the Row view's lifetime).
 * @param {{columns: object[], now?: () => number,
 *   disabledReason?: () => string|null}} opts
 *   `columns` = layoutColumns(model) (name, editable, section)
 * @returns {{accept: (event: MessageEvent, frameWindow: Window) => object|null,
 *   propose: (msg: object) => {column: object, value: *}|{refused: string},
 *   count: () => number}}
 */
export function createBridge({
  columns,
  now = () => Date.now(),
  disabledReason = () => null,
}) {
  let last = -Infinity;
  let count = 0;
  let cols = columns;
  return {
    setColumns: (next) => {
      cols = next;
    },
    /** The parsed message, or null when it is not from the renderer's window or unknown. */
    accept(event, frameWindow) {
      if (!frameWindow || event?.source !== frameWindow) return null;
      return parseMessage(event.data);
    },
    /** Check one proposal; on success it counts against the limits. */
    propose(msg) {
      const reason = disabledReason();
      if (reason) return { refused: `Refused: ${reason}.` };
      const col = cols.find((c) => c.name === msg.column);
      if (!col)
        return {
          refused: `Refused: column ${quote(msg.column)} doesn't exist.`,
        };
      if (!col.editable)
        return {
          refused: `Refused: ${quote(col.name)} is generated by a run and can't be changed here.`,
        };
      const t = now();
      if (count >= PROPOSE_LIMITS.perSession)
        return {
          refused: `Refused: a renderer can propose ${PROPOSE_LIMITS.perSession} changes per Row view; close and reopen the row to go on.`,
        };
      if (t - last < PROPOSE_LIMITS.intervalMs)
        return { refused: "Refused: one change every half second." };
      last = t;
      count += 1;
      return { column: col, value: msg.value };
    },
    count: () => count,
  };
}
