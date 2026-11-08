/* Demo Tracing data for the hosted prototype: a week of traces and sessions
   for each sample project, in the shapes the Tracing lists read, so traces
   can be picked and turned into environment tests. */
const T0 = Date.parse("2026-11-07T09:00:00Z");

/* What callers ask, by the kind of line the project traces. */
const ASKS = [
  [/book|rebook|airline|travel/, ["I need to move my flight to Friday", "My connection was cancelled, can you rebook me?", "Can I change the name on my booking?", "Is there a later flight with seats left?", "I want a refund for the cancelled leg", "Can I add a bag to my booking?"]],
  [/clinic|appoint/, ["I need to reschedule my appointment", "Is Dr. Patel free on Thursday morning?", "Can I cancel tomorrow's visit?", "Do you take my insurance?", "I need a follow-up after my blood test", "Can I book for my son as well?"]],
  [/support|billing|bank|copilot/, ["I was charged twice this month", "How do I update my card?", "Why did my bill go up?", "Can you cancel my subscription?", "I can't log in to my account", "Please send me last month's invoice"]],
  [/pickup|dispatch/, ["Where is my driver?", "Can I change the pickup address?", "The driver hasn't arrived yet", "I need a bigger vehicle", "Can I schedule a pickup for tomorrow?", "My delivery went to the wrong address"]],
  [/./, ["I need to return the blender I bought last week", "My refund still hasn't arrived", "Can I exchange this for a different size?", "The parcel arrived damaged", "I was charged twice for one order", "How long do refunds take?"]],
];
const asksOf = (pid) => ASKS.find(([re]) => re.test(String(pid || "")))[1];
const ANSWER = "I've started that for you. You'll get an email with the next steps shortly.";
const FAILED = "Sorry, I can't help with that.";

const short = (pid) => String(pid || "proj");
const traceId = (pid, i) => `${short(pid)}-tr-${1000 + i}`;

const tracesOf = (pid) =>
  Array.from({ length: 24 }, (_, i) => {
    const start = T0 - i * 47 * 6e4;
    const latency = 900 + ((i * 137) % 2400);
    return {
      trace_id: traceId(pid, i),
      id: traceId(pid, i),
      trace_name: "conversation",
      input: asksOf(pid)[i % 6],
      output: i % 5 === 3 ? FAILED : ANSWER,
      start_time: new Date(start).toISOString(),
      end_time: new Date(start + latency).toISOString(),
      latency,
      total_tokens: 600 + ((i * 53) % 900),
      cost: +(0.002 + (i % 7) * 0.0011).toFixed(4),
      status: i % 5 === 3 ? "ERROR" : "OK",
      session_id: `sess-${short(pid)}-${Math.floor(i / 3)}`,
    };
  });

const TRACE_COLUMNS = [
  ["trace_name", "Trace Name"],
  ["input", "Input"],
  ["output", "Output"],
  ["start_time", "Start Time"],
  ["latency", "Latency"],
  ["total_tokens", "Tokens"],
  ["cost", "Cost"],
  ["status", "Status"],
].map(([id, name]) => ({ id, name, is_visible: true, group_by: "System Metrics" }));

const meta = (n) => ({ total_rows: n, total_rows_exact: n, has_more: false, next_cursor: null, query_complete: true, query_status: "complete", query_sampled: false });

const projectOf = (q, path) => q.project_id || q.projectId || path?.split("/").filter(Boolean).pop();

export const traceList = (q) => {
  const rows = tracesOf(projectOf(q));
  return { status: true, result: { metadata: meta(rows.length), table: rows, config: TRACE_COLUMNS } };
};

export const sessionList = (q) => {
  const t = tracesOf(projectOf(q));
  const rows = Array.from({ length: 8 }, (_, k) => ({
    session_id: t[k * 3].session_id,
    session_name: null,
    start_time: t[k * 3 + 2].start_time,
    end_time: t[k * 3].end_time,
    created_at: t[k * 3 + 2].start_time,
    duration: 120 + k * 17,
    total_cost: +(t[k * 3].cost * 3).toFixed(4),
    total_tokens: t[k * 3].total_tokens * 3,
    total_traces_count: 3,
    first_message: t[k * 3 + 2].input,
    last_message: t[k * 3].output,
  }));
  return { status: true, result: { metadata: meta(rows.length), table: rows, config: [] } };
};

export const graph = (q) => ({
  status: true,
  result: {
    metric_name: q.property || q.metric || "latency",
    name: "Latency",
    data: Array.from({ length: 28 }, (_, i) => ({
      timestamp: new Date(T0 - (27 - i) * 6 * 36e5).toISOString(),
      value: 1200 + ((i * 37) % 9) * 110,
      primary_traffic: 6 + ((i * 11) % 7),
    })),
    query_complete: true,
    query_status: "complete",
    query_sampled: false,
  },
});

export const projectDetail = (path, projects) => {
  const id = path.split("/").filter(Boolean).pop();
  const p = projects.find((x) => x.id === id);
  return { status: true, result: { id, name: p?.name || id, trace_type: "observe", source: p?.source || "prototype", config: [], created_at: "2026-10-01T09:00:00Z" } };
};

/* One trace opened in the drawer: the conversation as a root span with its LLM call. */
export const traceDetail = (path) => {
  const id = path.split("/").filter(Boolean).pop();
  const i = Number(id.split("-").pop()) - 1000 || 0;
  const t = tracesOf(id.split("-tr-")[0])[Math.max(0, Math.min(23, i))] || tracesOf("demo")[0];
  const span = (sid, name, type, extra = {}) => ({
    id: sid, name, observation_type: type, input: t.input, output: t.output,
    start_time: t.start_time, end_time: t.end_time, latency_ms: t.latency, total_tokens: t.total_tokens,
    cost: t.cost, status: t.status, ...extra,
  });
  return {
    status: true,
    result: {
      trace: { id, name: t.trace_name, input: t.input, output: t.output, start_time: t.start_time, end_time: t.end_time, session_id: t.session_id, project: null },
      observation_spans: [
        { observation_span: span(`${id}-root`, "conversation", "chain"), children: [
          { observation_span: span(`${id}-llm`, "llm call", "llm", { model: "gpt-4o-mini", parent_span_id: `${id}-root` }), children: [] },
        ] },
      ],
      summary: { total_spans: 2, total_tokens: t.total_tokens, total_cost: t.cost, latency_ms: t.latency },
    },
  };
};
