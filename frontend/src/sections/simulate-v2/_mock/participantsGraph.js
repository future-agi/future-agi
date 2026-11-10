/*
  A call's participants record turned into the span tree the trace drawer
  already reads (TraceTreeV2, buildTraceGraph → AgentGraph), plus the three
  graph additions from the Participants view proposal (internal-docs,
  participants-view-proposal.md §4):
    1. step numbers on edges, in recorded order across the whole call
       ("2, 6" is a loop; siblings handed work at once read 3a / 3b);
       a control pass (transferred) is a dashed edge
    2. declared workers that never ran as "Not run" ghosts, more than three
       grouped into one node
    3. end states on the node, worded as the record says

  Each participant carries the graph's own node id and parent id, so the
  graph has one node per participant: the simulated user, the coordinator,
  each worker, each tool (per worker).
*/
import { buildTraceGraph } from "src/components/traceDetail/buildTraceGraph";
import {
  NOT_DECLARED,
  NOT_TIED,
  fullAnswerOf,
  handoffMessageOf,
} from "./participants";

export const USER_NODE = "user";
export const NOT_RUN = "Not run";
const BASE = Date.parse("2026-11-10T09:00:00Z");
const iso = (s) => new Date(BASE + Math.round((s || 0) * 1000)).toISOString();
const short = (s, n = 44) =>
  !s ? "" : s.length > n ? `${s.slice(0, n).trimEnd()}…` : s;

/* What a worker's node says about how it ended — the record's own words. */
export const endStateOf = (w, { undeclared = false } = {}) => {
  const end =
    w.state === "timeout"
      ? { label: "No answer came back", tone: "error" }
      : w.state === "unobserved"
        ? { label: "No events", tone: "neutral", ghost: true }
        : /* A failed or refused tool call is marked on the tool's own node;
             the worker reads Failed only when the record says it failed. */
          w.state === "failed" || w.failed
          ? { label: "Failed", tone: "error" }
          : null;
  if (!undeclared && !w.undeclared) return end;
  return end
    ? { ...end, label: `${NOT_DECLARED} · ${end.label}` }
    : { label: NOT_DECLARED, tone: "neutral" };
};

const callState = (c) =>
  c.blocked
    ? { label: "Refused", tone: "error" }
    : c.failed
      ? { label: "Failed", tone: "error" }
      : null;

const span = (id, name, type, o = {}) => ({
  observation_span: {
    id,
    name,
    observation_type: type,
    start_time: iso(o.start),
    end_time: iso(o.end ?? o.start),
    latency_ms: Math.max(0, Math.round(((o.end ?? o.start) - o.start) * 1000)),
    status: o.error ? "ERROR" : "OK",
    input: o.input ?? null,
    output: o.output ?? null,
    model: o.model ?? null,
    total_tokens: o.tokens ?? 0,
    span_attributes: {
      ...(o.node && { "graph.node.id": o.node }),
      ...(o.parent && { "graph.node.parent_id": o.parent }),
      ...(o.nodeName && { "graph.node.name": o.nodeName }),
      ...o.attrs,
    },
  },
  children: o.children || [],
});

/**
 * The record → { spans, graph, nodeOfSpan }.
 * spans: the span tree (Call → Turn → coordinator → workers → tools).
 * graph: buildTraceGraph(spans) with the three additions applied.
 */
export const participantsGraphOf = (p) => {
  if (!p) return null;
  const coord = p.coordinator;
  const undeclaredIds = new Set((p.undeclared || []).map((w) => w.id));
  const states = {}; // graph node id → end state
  const steps = []; // { at, edge, kind, parallel }
  const control = new Set(); // edge keys that are a control pass
  const nodeOfSpan = {};
  const add = (at, from, to, kind, group) =>
    steps.push({ at: at ?? 0, edge: `${from}->${to}`, kind, group });

  const toolSpan = (t, c, owner, ownerName) => {
    const node = `${owner}::${c.name}`;
    const st = callState(c);
    if (st) states[node] = st;
    if (owner === coord.id && c.workerName == null && !c.own)
      states[node] = states[node] || { label: NOT_TIED, tone: "neutral" };
    add(c.at, owner, node, "tool");
    const id = `t${t.n}:${c.id}`;
    nodeOfSpan[id] = node;
    return span(id, c.name, "tool", {
      node,
      parent: owner,
      nodeName: c.name,
      start: c.at,
      end: c.at + (c.durationMs ? c.durationMs / 1000 : 0),
      error: !!st,
      input: c.input,
      output: c.output,
      attrs: { "worker.name": ownerName },
    });
  };

  /* One node per worker: the API record gives each hand-off its own id
     (memberId is the worker), so a worker asked twice stays one node. */
  const blockNode = Object.fromEntries(
    [...(p.workers || []), ...(p.undeclared || [])].map((w) => [
      w.id,
      w.memberId || w.id,
    ]),
  );
  const workerSpan = (t, w, turnWorkers) => {
    const node = blockNode[w.id] || w.id;
    const parent = (w.parentId && blockNode[w.parentId]) || w.parentId || coord.id;
    const st = endStateOf(w, { undeclared: undeclaredIds.has(w.id) });
    if (st && states[node]?.tone !== "error") states[node] = st;
    if (w.transfer || w.state === "transferred")
      control.add(`${parent}->${node}`);
    add(w.delegatedAt, parent, node, "handoff", w.parallelWith ? parent : null);
    const subs = turnWorkers.filter((x) => x.parentId === w.id);
    const id = `t${t.n}:${w.id}`;
    nodeOfSpan[id] = node;
    return span(id, w.name, "agent", {
      node,
      parent,
      nodeName: w.name,
      start: w.delegatedAt,
      end: w.endedAt ?? w.delegatedAt,
      error: st?.tone === "error",
      input: handoffMessageOf(w) || w.task || null,
      output: fullAnswerOf(w),
      model: w.model,
      attrs: {
        ...(w.wid && { "worker.id": w.wid }),
        ...(st && { "worker.end_state": st.label }),
      },
      children: [
        ...subs.map((x) => workerSpan(t, x, turnWorkers)),
        ...(w.calls || []).map((c) => toolSpan(t, c, node, w.name)),
      ],
    });
  };

  const everyTurnWorker = (t) => [
    ...t.workers,
    ...(p.undeclared || []).filter(
      (u) => (u.turn ?? 0) === t.n - 1 && !t.workers.includes(u),
    ),
  ];

  const turns = p.turns.map((t) => {
    const ws = everyTurnWorker(t);
    const start = t.at ?? ws[0]?.delegatedAt ?? 0;
    const end = t.replyAt ?? Math.max(start, ...ws.map((w) => w.endedAt || 0));
    if (t.request) add(start, USER_NODE, coord.id, "turn");
    const coordId = `t${t.n}:coord`;
    nodeOfSpan[coordId] = coord.id;
    const own = (t.own || []).map((c) => ({ ...c, own: true }));
    const coordSpan = span(coordId, coord.name, "agent", {
      node: coord.id,
      parent: USER_NODE,
      nodeName: coord.name,
      start,
      end,
      input: t.request,
      output: t.reply,
      children: [
        ...ws
          .filter((w) => !w.parentId)
          .map((w) => workerSpan(t, w, ws)),
        ...(t.untied || []).map((c) => toolSpan(t, c, coord.id, coord.name)),
        ...own.map((c) => toolSpan(t, c, coord.id, coord.name)),
      ],
    });
    const turnId = `t${t.n}`;
    nodeOfSpan[turnId] = USER_NODE;
    return span(
      turnId,
      t.request ? `Turn ${t.n} · "${short(t.request)}"` : `Turn ${t.n}`,
      "chain",
      {
        node: USER_NODE,
        nodeName: "Simulated user",
        start,
        end,
        input: t.request,
        output: t.reply,
        children: [coordSpan],
      },
    );
  });
  const callEnd = Math.max(
    0,
    ...turns.map((s) => (Date.parse(s.observation_span.end_time) - BASE) / 1000),
  );
  const spans = [
    span("call", "Call (whole session)", "chain", {
      start: 0,
      end: callEnd,
      input: p.request,
      output: p.reply,
      children: turns,
    }),
  ];

  /* 1 · Step numbers in recorded order; hand-offs from one parent that ran
     in parallel share a number, lettered a, b. */
  const ordered = steps
    .map((s, i) => ({ ...s, i }))
    .sort((a, b) => a.at - b.at || a.i - b.i);
  let n = 0;
  ordered.forEach((s, i) => {
    const prev = ordered[i - 1];
    const together =
      s.group && prev?.group === s.group && Math.abs(prev.at - s.at) < 1.5;
    if (!together) n += 1;
    s.n = n;
  });
  const size = {};
  ordered.forEach((s) => (size[s.n] = (size[s.n] || 0) + 1));
  const seen = {};
  const labels = {};
  ordered.forEach((s) => {
    const k = (seen[s.n] = (seen[s.n] ?? -1) + 1);
    const mark = size[s.n] > 1 ? `${s.n}${String.fromCharCode(97 + k)}` : `${s.n}`;
    (labels[s.edge] = labels[s.edge] || []).push(mark);
  });

  const graph = buildTraceGraph(spans);
  graph.edges = graph.edges.map((e) => {
    const key = `${e.source}->${e.target}`;
    return labels[key] || control.has(key)
      ? {
          ...e,
          ...(labels[key] && { _stepLabel: labels[key].join(", ") }),
          ...(control.has(key) && { _control: true }),
        }
      : e;
  });
  graph.nodes = graph.nodes.map((nd) =>
    states[nd.id] ? { ...nd, _state: states[nd.id] } : nd,
  );

  /* 2 · Declared workers that never ran: ghosts under the coordinator. */
  const notRun = p.observed ? p.notRun || [] : [];
  const ghosts =
    notRun.length > 3
      ? [{ id: "__not_run__", name: `${notRun.length} workers`, names: notRun }]
      : notRun.map((name) => ({ id: `__not_run__${name}`, name, names: [name] }));
  ghosts.forEach((g) => {
    graph.nodes.push({
      id: g.id,
      name: g.name,
      type: "agent",
      span_count: 0,
      avg_latency_ms: 0,
      total_tokens: 0,
      total_cost: 0,
      error_count: 0,
      trace_count: 1,
      _state: { label: NOT_RUN, tone: "neutral", ghost: true },
      _notRunNames: g.names,
    });
    graph.edges.push({
      source: coord.id,
      target: g.id,
      transition_count: 0,
      avg_latency_ms: 0,
      total_tokens: 0,
      total_cost: 0,
      error_count: 0,
      trace_count: 1,
      is_self_loop: false,
      _ghost: true,
    });
  });

  return { spans, graph, nodeOfSpan };
};

/** The span tree cut to one participant: its turns, the path to it, and
    everything under it. */
export const filterSpansToNode = (spans, node) => {
  const nodeOf = (e) =>
    e.observation_span?.span_attributes?.["graph.node.id"] || null;
  const walk = (entries) =>
    entries
      .map((e) => {
        if (nodeOf(e) === node) return e;
        const kids = walk(e.children || []);
        return kids.length ? { ...e, children: kids } : null;
      })
      .filter(Boolean);
  return walk(spans);
};
