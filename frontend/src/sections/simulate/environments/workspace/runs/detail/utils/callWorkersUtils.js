import { isHandOff } from "src/api/simulate-environments/runDetail";

// About six lines of text; a longer answer opens on request.
const CLAMP_LINES = 6;
const CLAMP_CHARS = 480;
const HAS_ZONE = /(?:Z|[+-]\d{2}(?::?\d{2})?)$/i;
// A printed dict opens on a key; a printed list on a quoted, nested or literal item. `…` ends a cut-off one.
const PRINTED_DICT = /^\{\s*(?:(['"]).*?\1|\w+)\s*:/;
const PRINTED_LIST =
  /^\[\s*(?:['"{[]|(?:-?\d[\d.]*|True|False|None)\s*(?:,|\]$)|\]$)/;

/** Epoch seconds of a stored timestamp, read as UTC when it carries no zone; null when it is not one. */
export const seconds = (iso) => {
  const value = iso ? Date.parse(HAS_ZONE.test(iso) ? iso : `${iso}Z`) : NaN;
  return Number.isNaN(value) ? null : value / 1000;
};

/** Whether an answer is long enough to fold. */
export const isLong = (text) =>
  text.split("\n").length > CLAMP_LINES || text.length > CLAMP_CHARS;

const looksPrinted = (body) =>
  /[}\]…]$/.test(body) && (PRINTED_DICT.test(body) || PRINTED_LIST.test(body));

/** An answer returned as data (JSON, or a printed dict or list) as a code block; other text as it came. */
export const asShown = (text) => {
  const body = text.trim();
  if (!/^[[{]/.test(body)) return text;
  let code;
  try {
    code = JSON.stringify(JSON.parse(body), null, 2);
  } catch {
    // Prose that opens with a bracket stays text; a printed or cut-off object stays as it came.
    if (!looksPrinted(body)) return text;
    code = body;
  }
  return `\`\`\`\n${code}\n\`\`\``;
};

/** Events in order as a tree under the coordinator: each hand-off under the agent that made it (`by`). */
export const arrange = (events) => {
  const root = { items: [], depth: 0 };
  const open = {}; // worker -> hand-offs not returned yet, latest last
  const latest = {}; // worker -> its latest hand-off
  const blocks = [];
  // The open hand-off an event belongs to: the one it names, else the worker's latest when it names none.
  const openFor = (event) => {
    const waiting = open[event.worker_id] || [];
    if (!event.delegation_id) return waiting[waiting.length - 1];
    return waiting.find(
      (one) => one.delegated.delegation_id === event.delegation_id,
    );
  };
  const holder = (worker, at) => {
    if (!worker) return root;
    const waiting = open[worker] || [];
    const containing = [...waiting]
      .reverse()
      .find((one) => at === null || one.from === null || one.from <= at);
    return containing || latest[worker] || root;
  };
  for (const event of events) {
    const at = seconds(event?.at);
    if (isHandOff(event)) {
      const parent = holder(event.by, at);
      const block = {
        kind: "handoff",
        key: event.seq,
        worker_id: event.worker_id,
        delegated: event,
        transfer: event.type === "transferred",
        items: [],
        depth: parent.depth + 1,
        from: at,
        to: at,
      };
      parent.items.push(block);
      blocks.push(block);
      (open[event.worker_id] ||= []).push(block);
      latest[event.worker_id] = block;
    } else if (event.type === "returned") {
      const block = openFor(event);
      if (block) {
        open[event.worker_id].splice(open[event.worker_id].indexOf(block), 1);
        block.returned = event;
        if (at !== null) block.to = Math.max(block.to ?? at, at);
      } else {
        const orphan = {
          kind: "handoff",
          key: event.seq,
          worker_id: event.worker_id,
          items: [],
          returned: event,
          depth: 1,
          from: at,
          to: at,
        };
        root.items.push(orphan);
        blocks.push(orphan);
      }
    } else {
      // A call with no worker is the coordinator's own, even with control passed back to it.
      const block = event.worker_id ? openFor(event) : undefined;
      if (block) {
        block.items.push({ kind: "call", key: event.seq, event });
        if (at !== null) block.to = Math.max(block.to ?? at, at);
      } else root.items.push({ kind: "call", key: event.seq, event });
    }
  }
  // A hand-off ends when it returned; a block still open ends with its last step, and its children's.
  // One with no start of its own starts at its first step or child.
  const close = (node) => {
    for (const item of node.items)
      if (item.kind === "handoff") {
        close(item);
        if (item.to !== null && node.to !== undefined)
          node.to = node.to === null ? item.to : Math.max(node.to, item.to);
      }
    if (node.from === null)
      for (const item of node.items) {
        const first =
          item.kind === "handoff" ? item.from : seconds(item.event.at);
        if (first !== null) {
          node.from = first;
          break;
        }
      }
  };
  close(root);
  // Siblings from the same agent whose spans overlap ran in parallel; one with no known start can't be compared.
  let parallel = 0;
  let overlapUnknown = false;
  const mark = (node) => {
    const siblings = node.items.filter((item) => item.kind === "handoff");
    siblings.forEach((one, index) => {
      siblings.slice(index + 1).forEach((other) => {
        if (one.from === null || other.from === null) overlapUnknown = true;
        if (
          one.from !== null &&
          other.from !== null &&
          other.from < (one.to ?? one.from) &&
          one.from < (other.to ?? other.from)
        ) {
          if (!one.parallel) parallel += 1;
          if (!other.parallel) parallel += 1;
          one.parallel = true;
          other.parallel = true;
        }
      });
      mark(one);
    });
  };
  mark(root);
  const depth = blocks.reduce((most, one) => Math.max(most, one.depth), 0);
  const depthOf = {};
  for (const one of blocks)
    if (!(one.worker_id in depthOf)) depthOf[one.worker_id] = one.depth;
  return { items: root.items, depth, parallel, overlapUnknown, depthOf };
};
