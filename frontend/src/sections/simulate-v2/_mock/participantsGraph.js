/*
  A call's participants record turned into the span tree the trace drawer
  already reads (TraceTreeV2, buildTraceGraph → AgentGraph), plus the three
  graph additions from the Participants view proposal (internal-docs,
  participants-view-proposal.md §4):
    1. the path the work took: each participant numbered in the order it
       first acted, and the path as those numbers (1 → 2 → 3 → 1 is a
       loop; siblings handed work at once share a step) — Chintan's
       canvas comment, 10 Nov, in place of step numbers on edges;
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
/* callerName: the actor on the call (glossary: an Actor is the simulated
   person; a Persona is how they behave) — in place of "Simulated user". */
export const participantsGraphOf = (p, { callerName = null } = {}) => {
  if (!p) return null;
  const coord = p.coordinator;
  const undeclaredIds = new Set((p.undeclared || []).map((w) => w.id));
  const states = {}; // graph node id → end state
  const steps = []; // { at, edge, kind, parallel }
  const control = new Set(); // edge keys that are a control pass
  /* Answers that came back up: worker → whoever handed it the work, and the
     coordinator's reply → the caller. Only recorded returns; a worker that
     timed out, took over the call or left no events returns nothing. */
  const returns = {}; // "from->to" → count
  const addReturn = (from, to) => {
    const k = `${from}->${to}`;
    returns[k] = (returns[k] || 0) + 1;
  };
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
    const parent =
      (w.parentId && blockNode[w.parentId]) || w.parentId || coord.id;
    const st = endStateOf(w, { undeclared: undeclaredIds.has(w.id) });
    if (st && states[node]?.tone !== "error") states[node] = st;
    if (w.transfer || w.state === "transferred")
      control.add(`${parent}->${node}`);
    else if (
      w.endedAt != null &&
      w.state !== "timeout" &&
      w.state !== "unobserved"
    )
      addReturn(node, parent);
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
    /* A turn lasts until its reply ends, or its last worker does. */
    const end = Math.max(
      t.endAt ?? t.replyAt ?? start,
      ...ws.map((w) => w.endedAt || 0),
    );
    if (t.request) add(start, USER_NODE, coord.id, "turn");
    if (t.reply) addReturn(coord.id, USER_NODE);
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
        ...ws.filter((w) => !w.parentId).map((w) => workerSpan(t, w, ws)),
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
        nodeName: callerName || "Simulated user",
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
    ...turns.map(
      (s) => (Date.parse(s.observation_span.end_time) - BASE) / 1000,
    ),
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

  /* 1 · Moves in recorded order; hand-offs from one parent that ran in
     parallel share a number. */
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

  /* The path the work took, as Chintan reads it (10 Nov): each participant
     gets a number in the order it first acted, and the path lists those
     numbers in the order the work moved, so a loop reads 1 → 2 → 3 → 1.
     Siblings handed work at once share one step. Tools aren't steps. */
  const moves = ordered.filter((st) => st.kind !== "tool");
  const path = [];
  /* Who holds the work now: one participant, or a group working at once. */
  let at = [];
  moves.forEach((st) => {
    const [from, to] = st.edge.split("->");
    const together = moves.filter((x) => x.n === st.n);
    if (together.length > 1 && together[0] !== st) return;
    if (!at.includes(from)) path.push({ ids: [from] });
    const ids = together.map((x) => x.edge.split("->")[1]);
    path.push(together.length > 1 ? { ids, together: true } : { ids: [to] });
    at = together.length > 1 ? ids : [to];
  });
  const numberOf = {};
  path.forEach((stop) =>
    stop.ids.forEach((id) => {
      if (!(id in numberOf)) numberOf[id] = Object.keys(numberOf).length + 1;
    }),
  );

  const graph = buildTraceGraph(spans);
  graph.edges = graph.edges.map((e) => {
    const key = `${e.source}->${e.target}`;
    return control.has(key) ? { ...e, _control: true } : e;
  });
  graph.nodes = graph.nodes.map((nd) => ({
    ...nd,
    ...(numberOf[nd.id] && { name: `${numberOf[nd.id]} · ${nd.name}` }),
    ...(states[nd.id] && { _state: states[nd.id] }),
  }));

  /* 2 · Declared workers that never ran: ghosts under the coordinator. */
  const notRun = p.observed ? p.notRun || [] : [];
  const ghosts =
    notRun.length > 3
      ? [{ id: "__not_run__", name: `${notRun.length} workers`, names: notRun }]
      : notRun.map((name) => ({
          id: `__not_run__${name}`,
          name,
          names: [name],
        }));
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

  const nodeIds = new Set(graph.nodes.map((nd) => nd.id));
  Object.entries(returns).forEach(([k, count]) => {
    const [source, target] = k.split("->");
    if (!nodeIds.has(source) || !nodeIds.has(target)) return;
    graph.edges.push({
      source,
      target,
      transition_count: count,
      avg_latency_ms: 0,
      total_tokens: 0,
      total_cost: 0,
      error_count: 0,
      trace_count: 1,
      is_self_loop: false,
      _return: true,
    });
  });

  /* Each stop: { ids, together }, with each id's number and name. */
  const nameOf = Object.fromEntries(
    buildTraceGraph(spans).nodes.map((nd) => [nd.id, nd.name]),
  );
  const pathStops = path.map((stop) => ({
    together: !!stop.together,
    who: stop.ids.map((id) => ({
      id,
      n: numberOf[id],
      name: nameOf[id] || id,
    })),
  }));

  /* Read for people, not traces (12 Nov): no Start/End — the caller's node
     is where the call begins and ends — full names, each node's role in
     place of a span count, and the caller's repeat reads as turns. */
  const isSentinel = (id) => id === "__start__" || id === "__end__";
  const roleOf = (nd) => {
    const n = nd.span_count || 0;
    /* Its turns are on the arrow down to the coordinator. */
    if (nd.id === USER_NODE) return "Caller";
    if (nd._state?.ghost) return "Declared worker";
    /* The coordinator answers every turn; the caller's node counts them. */
    if (nd.id === coord.id) return "Coordinator";
    const base = nd.type === "tool" ? "Tool" : "Worker";
    const verb = nd.type === "tool" ? "called" : "asked";
    return n > 1 ? `${base} · ${verb} ${n}×` : base;
  };
  graph.nodes = graph.nodes
    .filter((nd) => !isSentinel(nd.id))
    .map((nd) => ({ ...nd, _wide: true, _sub: roleOf(nd) }));
  graph.edges = graph.edges
    .filter((e) => !isSentinel(e.source) && !isSentinel(e.target))
    .map((e) =>
      e.transition_count <= 1
        ? e
        : e.source === USER_NODE && !e._return
          ? { ...e, _countLabel: `${e.transition_count} turns` }
          : e.target === USER_NODE && e._return
            ? { ...e, _countLabel: `${e.transition_count} replies` }
            : e,
    );

  /* The same two stops back to back (a turn the coordinator answered alone,
     again and again) fold into one pair with a count: 1 → 2 ×5. */
  const keyOf = (st) => st.who.map((w) => w.id).join("+");
  const folded = [];
  for (let i = 0; i < pathStops.length; ) {
    const a = pathStops[i];
    const b = pathStops[i + 1];
    const n = folded.length;
    if (
      b &&
      n >= 2 &&
      keyOf(folded[n - 2]) === keyOf(a) &&
      keyOf(folded[n - 1]) === keyOf(b)
    ) {
      folded[n - 1].repeat = (folded[n - 1].repeat || 1) + 1;
      i += 2;
    } else {
      folded.push({ ...a });
      i += 1;
    }
  }

  return { spans, graph, nodeOfSpan, path: folded };
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
