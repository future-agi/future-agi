import { isHandOff } from "src/api/simulate-environments/runDetail";

// About six lines of text; a longer answer opens on request.
const CLAMP_LINES = 6;
const CLAMP_CHARS = 480;

/** Epoch seconds of a stored timestamp, read as UTC when it carries no zone; null when it is not one. */
export const seconds = (iso) => {
  const value = iso ? Date.parse(iso.endsWith("Z") ? iso : `${iso}Z`) : NaN;
  return Number.isNaN(value) ? null : value / 1000;
};

/** Whether an answer is long enough to fold. */
export const isLong = (text) =>
  text.split("\n").length > CLAMP_LINES || text.length > CLAMP_CHARS;

/** An answer returned as data (a JSON object, or a printed dict or list) as a code block; other text as it came. */
export const asShown = (text) => {
  if (!/^\s*[[{]/.test(text)) return text;
  let body = text.trim();
  try {
    body = JSON.stringify(JSON.parse(body), null, 2);
  } catch {
    // A printed Python dict or a cut-off object stays as it came.
  }
  return `\`\`\`\n${body}\n\`\`\``;
};

/** Events in order as a tree under the coordinator: each hand-off under the agent that made it (`by`). */
export const arrange = (events) => {
  const root = { items: [], depth: 0 };
  const open = {}; // worker -> hand-offs not returned yet, latest last
  const latest = {}; // worker -> its latest hand-off
  const blocks = [];
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
      const waiting = open[event.worker_id] || [];
      const block = waiting.pop();
      if (block) {
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
      const waiting = event.worker_id ? open[event.worker_id] || [] : [];
      const block = waiting[waiting.length - 1];
      if (block) {
        block.items.push({ kind: "call", key: event.seq, event });
        if (at !== null) block.to = Math.max(block.to ?? at, at);
      } else root.items.push({ kind: "call", key: event.seq, event });
    }
  }
  // A hand-off ends when it returned; a block still open ends with its last step, and its children's.
  const close = (node) => {
    for (const item of node.items)
      if (item.kind === "handoff") {
        close(item);
        if (item.to !== null && node.to !== undefined)
          node.to = node.to === null ? item.to : Math.max(node.to, item.to);
      }
  };
  close(root);
  // Siblings from the same agent whose spans overlap ran in parallel.
  let parallel = 0;
  const mark = (node) => {
    const siblings = node.items.filter((item) => item.kind === "handoff");
    siblings.forEach((one, index) => {
      siblings.slice(index + 1).forEach((other) => {
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
  return { items: root.items, depth, parallel, depthOf };
};
