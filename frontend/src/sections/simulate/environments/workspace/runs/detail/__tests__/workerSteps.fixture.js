// Test fixture only: builds the API's arranged `steps` for a hand-written `events` list, so panel tests can stay
// written as events. The API computes these (simulate/services/harness_worker_steps.py, tested there).
import { isHandOff } from "src/api/simulate-environments/runDetail";

const HAS_ZONE = /(?:Z|[+-]\d{2}(?::?\d{2})?)$/i;

const seconds = (iso) => {
  const value = iso ? Date.parse(HAS_ZONE.test(iso) ? iso : `${iso}Z`) : NaN;
  return Number.isNaN(value) ? null : value / 1000;
};

/** Events in order as a tree under the coordinator: each hand-off under the agent that made it (`by`). */
const arrange = (events) => {
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

const flatten = (items, parent, depth, out) => {
  for (const item of items) {
    if (item.kind === "handoff") {
      out.push({
        kind: "handoff",
        key: item.key,
        parent,
        depth: item.depth,
        worker_id: item.worker_id,
        transfer: Boolean(item.transfer),
        handoff: item.delegated ?? null,
        returned: item.returned ?? null,
        parallel: Boolean(item.parallel),
        turn: 0,
      });
      flatten(item.items, item.key, item.depth, out);
    } else {
      out.push({
        kind: "tool_call",
        key: item.key,
        parent,
        depth,
        worker_id: item.event.worker_id,
        call: item.event,
        turn: 0,
      });
    }
  }
  return out;
};

export const withSteps = (timeline) => {
  // A test that hands in the API's rows already keeps them.
  if (!timeline || timeline.steps) return timeline;
  const tree = arrange(timeline.events || []);
  return {
    ...timeline,
    steps: flatten(tree.items, null, 0, []),
    // One turn holding every step.
    turns: [
      {
        turn: 0,
        request: timeline.request ?? null,
        response: timeline.response ?? null,
      },
    ],
    depth: tree.depth,
    parallel: tree.parallel,
    overlap_unknown: tree.overlapUnknown,
    worker_depths: tree.depthOf,
  };
};
