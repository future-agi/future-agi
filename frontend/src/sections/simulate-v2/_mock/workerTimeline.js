/*
  The API's call record (`WorkerTimeline`, platform PR #3699) read into the
  Participants tab's shape — the same one participantsOf builds for seeded
  calls, so both render through one component.

  Only what the record carries is said: the coordinator's task text, the tool
  names, the worker's own return text. A hand-off with no return is "No answer
  came back"; nesting comes from `by`, parallel from overlapping spans of
  siblings. Model and tokens aren't in the record, so they stay empty.
*/
import { NOT_TIED, summaryOf } from "./participants";

const HAS_ZONE = /(?:Z|[+-]\d{2}(?::?\d{2})?)$/i;
const secs = (iso) => {
  const v = iso ? Date.parse(HAS_ZONE.test(iso) ? iso : `${iso}Z`) : NaN;
  return Number.isNaN(v) ? null : v / 1000;
};
const textOf = (v) =>
  v == null ? null : typeof v === "string" ? v : JSON.stringify(v);

/** A WorkerTimeline → the Participants model; null for no record. */
export const participantsFromTimeline = (tl) => {
  if (!tl) return null;
  const events = [...(tl.events || [])].sort((a, b) => a.seq - b.seq);
  const t0 =
    secs(tl.started_at) ??
    secs(tl.request?.at) ??
    events.map((e) => secs(e.at)).find((s) => s != null) ??
    null;
  const rel = (iso) => {
    const s = secs(iso);
    return s == null || t0 == null ? null : Math.round((s - t0) * 10) / 10;
  };
  const coordinator = {
    id: "coordinator",
    name: tl.coordinator?.name || "Coordinator",
  };
  const declared = Object.fromEntries(
    (tl.workers || []).map((w) => [w.worker_id, w]),
  );
  const nameOf = (id) =>
    declared[id]?.name ||
    String(id || "")
      .split("/")
      .pop() ||
    "Unknown";

  const blocks = [];
  const open = {};
  const latest = {};
  const untied = [];
  const own = [];
  const call = (e) => ({
    id: `call-${e.seq}`,
    name: e.tool,
    at: rel(e.at),
    input: e.input ?? null,
    output: e.output ?? null,
    failed: e.status === "error",
    blocked: false,
    recorded: true,
    workerName: e.worker_id ? nameOf(e.worker_id) : null,
  });
  events.forEach((e) => {
    if (e.type === "delegated" || e.type === "transferred") {
      const parent = e.by ? (open[e.by] || []).at(-1) || latest[e.by] : null;
      const b = {
        id: `${e.worker_id}#${e.seq}`,
        memberId: e.worker_id,
        name: nameOf(e.worker_id),
        wid: e.worker_id,
        model: null,
        recorded: true,
        undeclared: !!declared[e.worker_id]?.undeclared,
        transfer: e.type === "transferred",
        task: e.task ?? null,
        result: null,
        /* Control passed on doesn't hand back; anything else owes a return. */
        state: e.type === "transferred" ? "transferred" : "timeout",
        delegatedAt: rel(e.at),
        endedAt: null,
        parentId: parent?.id || null,
        parentName: parent?.name || null,
        depth: parent ? parent.depth + 1 : 0,
        parallelWith: null,
        calls: [],
      };
      blocks.push(b);
      (open[e.worker_id] ||= []).push(b);
      latest[e.worker_id] = b;
    } else if (e.type === "returned") {
      const waiting = open[e.worker_id] || [];
      const b =
        (e.delegation_id &&
          waiting.find((x) => x.delegation === e.delegation_id)) ||
        waiting.at(-1);
      if (b) {
        waiting.splice(waiting.indexOf(b), 1);
        b.state = "returned";
        b.result = textOf(e.result);
        b.endedAt = rel(e.at);
      }
    } else if (e.type === "tool_call") {
      if (e.worker_id) {
        const b = (open[e.worker_id] || []).at(-1) || latest[e.worker_id];
        if (b) b.calls.push(call(e));
        else untied.push(call(e));
      } else if (e.attribution === "unattributed") untied.push(call(e));
      else own.push(call(e));
    }
  });
  /* A hand-off with no start starts at its first call; one still open ends
     with its last step. */
  blocks.forEach((b) => {
    if (b.delegatedAt == null)
      b.delegatedAt = b.calls[0]?.at ?? b.endedAt ?? null;
    if (b.endedAt == null)
      b.endedAt = b.calls.at(-1)?.at ?? b.delegatedAt ?? null;
  });
  /* Siblings from the same agent whose spans overlap ran in parallel. */
  blocks.forEach((a) =>
    blocks.forEach((b) => {
      if (a === b || a.parentId !== b.parentId) return;
      if (a.delegatedAt == null || b.delegatedAt == null) return;
      const aEnd = a.endedAt ?? a.delegatedAt;
      const bEnd = b.endedAt ?? b.delegatedAt;
      if (b.delegatedAt < aEnd && a.delegatedAt < bEnd)
        a.parallelWith = a.parallelWith
          ? `${a.parallelWith}, ${b.name}`
          : b.name;
    }),
  );
  /* The table reads parents before their own sub-workers. */
  const ordered = [];
  const place = (parentId) =>
    blocks
      .filter((b) => b.parentId === parentId)
      .forEach((b) => {
        ordered.push(b);
        place(b.id);
      });
  place(null);

  const ran = new Set(blocks.map((b) => b.memberId));
  const quiet = (tl.workers || []).filter((w) => !ran.has(w.worker_id));
  const reply = tl.response?.text ?? null;
  const replyAt = rel(tl.response?.at);
  const allCalls = [...blocks.flatMap((b) => b.calls), ...untied, ...own];
  return {
    fromContract: true,
    summary: summaryOf({
      coordinator: coordinator.name,
      workers: ordered,
      observed: true,
    }),
    observed: true,
    enforced: false,
    traces: !!tl.spans_received,
    coordinator,
    outcome: ordered.some((b) => b.state === "timeout")
      ? "timeout"
      : "returned",
    turns: [
      {
        n: 1,
        request: tl.request?.text ?? null,
        reply,
        at: 0,
        replyAt,
        workers: ordered,
        untied,
        own,
      },
    ],
    workers: ordered,
    undeclared: [],
    untied,
    /* Declared but no events: either it didn't run or it ran unseen. */
    notRun: quiet.filter((w) => w.observed).map((w) => w.name),
    notObserved: quiet.filter((w) => !w.observed).map((w) => w.name),
    request: tl.request?.text ?? null,
    reply,
    duration: replyAt,
    eventsOmitted: tl.events_omitted || 0,
    steps: [],
    checks: [],
    boundaries: allCalls.map((c) => ({
      tool: c.name,
      worker: c.workerName || (own.includes(c) ? coordinator.name : NOT_TIED),
    })),
  };
};
