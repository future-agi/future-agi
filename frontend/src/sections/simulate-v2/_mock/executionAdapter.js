/**
 * A mock adapter for the legacy execution-detail screen.
 *
 * The revamped flow produces a run entirely in the browser: scenarios derived
 * from the agent, turns played by the run player, scores settled by the mock
 * graders. The execution-detail screen it lands on is real product code and
 * fetches everything it shows — KPIs, the row list, one call's transcript —
 * from `/simulate/test-executions/…`.
 *
 * Rather than fork that screen or thread a "mock mode" prop through a dozen
 * components, this intercepts at the one place every one of those requests
 * passes through: axios's adapter. A request for a prototype execution id is
 * answered from the run in the store; anything else is handed to the real
 * adapter untouched. So the legacy screen renders unmodified, and there is
 * exactly one seam to remove when a real backend exists.
 *
 * The id prefix is what makes this safe. Only ids minted by the revamped flow
 * carry it, so no real execution can ever be intercepted by accident.
 */
import axios from "axios";
import axiosInstance from "src/utils/axios";
import { getEnvironment } from "./environments";
import { resolveEval } from "./evals";
import { generatedPool } from "./scenarios";

/** Ids the revamped flow mints. Nothing else may start with this. */
export const PROTO_RUN_PREFIX = "sim2run-";

export const isProtoRun = (id) =>
  typeof id === "string" && id.startsWith(PROTO_RUN_PREFIX);

/** `sim2run-<envId>-<stamp>` — the environment travels inside the id. */
export const protoRunId = (envId, stamp) =>
  `${PROTO_RUN_PREFIX}${envId}--${stamp}`;

export const envIdOfProtoRun = (runId) =>
  isProtoRun(runId) ? runId.slice(PROTO_RUN_PREFIX.length).split("--")[0] : null;

/* ── the run being reported on ───────────────────────────────────────────── */

/*
  The live view hands its finished tasks over on the way out. Held in module
  scope rather than the persisted store because it is a report on one run, not
  environment state, and it is regenerated whenever a run is replayed.
*/
/** Deterministic per row, so a re-render never changes a number on screen. */
const hash = (s) => {
  let h = 0;
  for (let i = 0; i < String(s).length; i += 1) h = (h * 31 + String(s).charCodeAt(i)) >>> 0;
  return h;
};

const STORE_KEY = "fagi.simulate.lastRun.v1";

let lastRun = null;

export const publishRun = (runId, payload) => {
  lastRun = { runId, ...payload };
  /* Survives a reload of the report's URL, where module memory does not. */
  try {
    window.localStorage.setItem(STORE_KEY, JSON.stringify(lastRun));
  } catch { /* private mode, quota — the in-memory copy still serves */ }
};

const hydrate = () => {
  if (lastRun) return lastRun;
  try {
    lastRun = JSON.parse(window.localStorage.getItem(STORE_KEY) || "null");
  } catch { lastRun = null; }
  return lastRun;
};

/**
 * The run this report is about.
 *
 * Falls back to a synthesised run when there is nothing published for the id —
 * a shared link, a reload after the store was cleared, someone opening the URL
 * directly. An empty report reads as a broken screen; a plausible one reads as
 * a report, which is the whole point of a prototype.
 */
export const getPublishedRun = (runId) => {
  const held = hydrate();
  if (held && held.runId === runId) return held;
  return synthesiseRun(runId);
};

function synthesiseRun(runId) {
  const env = getEnvironment(envIdOfProtoRun(runId));
  if (!env) return null;
  const pool = generatedPool(env).slice(0, 12);
  if (!pool.length) return null;

  const evalIds = (env.evalPreset || []).length
    ? env.evalPreset
    : ["eval-task-success", "eval-policy-adherence", "eval-tone", "eval-latency"];

  return {
    runId,
    startedAt: new Date(Date.now() - 1000 * 60 * 27).toISOString(),
    synthesised: true,
    tasks: pool.map((row, i) => {
      const h = hash(row.id);
      const failing = i % 7 === 3;
      return {
        ...row,
        status: failing ? "failed" : "passed",
        steps: Array.from({ length: 4 + (h % 7) }, (_, n) => ({
          id: `${row.id}-s${n}`,
          role: n % 2 === 0 ? "customer" : "agent",
          text: n % 2 === 0 ? row.task : row.expected,
        })),
        evalResults: evalIds.map((id, k) => {
          const score = failing && k < 2
            ? 0.2 + ((h >> k) % 25) / 100
            : 0.72 + ((h >> k) % 26) / 100;
          return {
            id,
            name: resolveEval(id)?.name || id,
            score,
            passed: score >= 0.6,
            reason: score >= 0.6
              ? "Met the check on every turn it applied to."
              : "Broke the rule the environment enforces.",
          };
        }),
      };
    }),
  };
}

/* ── payload builders ────────────────────────────────────────────────────── */

const round = (n, dp = 2) => Number(n.toFixed(dp));

function runFor(runId) {
  const published = getPublishedRun(runId);
  const envId = envIdOfProtoRun(runId);
  const env = getEnvironment(envId);
  return { published, env, envId };
}

/**
 * KPIs.
 *
 * Keys are the snake_case ones `extractKpis` sorts into System Metrics, Call
 * Details and Evaluation Metrics — including the `{choices, …}` objects it
 * turns into the donut charts. Voice and chat emit different metric sets,
 * because the screen filters by `agent_type` and would show empty cards for
 * the wrong half.
 */
function kpisPayload(runId) {
  const { published, env } = runFor(runId);
  const tasks = published?.tasks || [];
  const voice = (env?.surface || "chat") === "voice";
  const total = tasks.length || 0;
  const connected = tasks.filter((t) => t.status !== "error").length;
  const passed = tasks.filter((t) => t.status === "passed").length;

  const avg = (fn) => (tasks.length ? tasks.reduce((a, t) => a + fn(t), 0) / tasks.length : 0);
  const turns = avg((t) => t.steps?.length || 0);

  const base = {
    agent_type: voice ? "voice" : "text",
    total_calls: total,
    connected_calls: connected,
    calls_connected_percentage: total ? Math.round((connected / total) * 100) : 0,
    calls_attempted: total,
    failed_calls: total - connected,
    total_duration: Math.round(turns * 11 * total),
    is_inbound: env?.direction === "inbound",
    avg_turn_count: round(turns, 1),
  };

  const metrics = voice
    ? {
        avg_score: round(3 + (passed / Math.max(total, 1)) * 1.8, 1),
        avg_agent_latency: Math.round(320 + (hash(runId) % 260)),
        avg_bot_wpm: Math.round(120 + (hash(runId) % 40)),
        avg_stop_time_after_interruption: Math.round(180 + (hash(runId) % 120)),
        agent_talk_percentage: 54,
        customer_talk_percentage: 46,
      }
    : {
        avg_total_tokens: Math.round(900 + (hash(runId) % 600)),
        avg_input_tokens: Math.round(600 + (hash(runId) % 400)),
        avg_output_tokens: Math.round(240 + (hash(runId) % 200)),
        avg_chat_latency_ms: Math.round(410 + (hash(runId) % 300)),
        avg_csat_score: round(3 + (passed / Math.max(total, 1)) * 1.8, 1),
      };

  /*
    One numeric entry per eval — these become the Evaluation Metrics bars — and
    one choice distribution, which is what the donut renders. Both are computed
    from the same task scores the run already settled, so the summary cannot
    disagree with the rows below it.
  */
  const evalMetrics = {};
  const distributions = {};
  const evalIds = tasks[0]?.evalResults?.map((r) => r.id) || [];

  evalIds.forEach((id, i) => {
    const scores = tasks
      .map((t) => t.evalResults?.find((r) => r.id === id)?.score)
      .filter((n) => typeof n === "number");
    const mean = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : 0;
    const name = resolveEval(id)?.name || id;

    if (i % 3 === 2) {
      const always = scores.filter((s) => s >= 0.8).length;
      const occasionally = scores.filter((s) => s >= 0.5 && s < 0.8).length;
      const never = scores.length - always - occasionally;
      distributions[`${id}_distribution`] = {
        choices: ["always", "occasionally", "never"],
        always,
        occasionally,
        never,
      };
    } else {
      evalMetrics[name] = round(mean * 100, 2);
    }
  });

  return { ...base, ...metrics, ...evalMetrics, ...distributions };
}

/** The run's own record — the header reads scenarios and created_at off this. */
function runDetailPayload(runId) {
  const { published, env } = runFor(runId);
  return {
    id: runId,
    name: env?.name || "Simulation",
    source_type: "scenario",
    created_at: published?.startedAt || new Date().toISOString(),
    scenarios: (published?.tasks || []).map((t) => ({ id: t.id, name: t.title })),
    agent_type: (env?.surface || "chat") === "voice" ? "voice" : "text",
  };
}

/**
 * The row list.
 *
 * `column_order` is what the grid builds its columns from, so the shape of the
 * table is decided here rather than in the grid: the fixed call columns first,
 * then one evaluation column per eval that actually ran.
 */
function listPayload(runId, params = {}) {
  const { published, env } = runFor(runId);
  const tasks = published?.tasks || [];
  const voice = (env?.surface || "chat") === "voice";

  const evalIds = tasks[0]?.evalResults?.map((r) => r.id) || [];
  const column_order = [
    { id: "timestamp", column_name: "Timestamp", type: "call_column" },
    { id: "call_details", column_name: "Call Details", type: "call_column" },
    { id: "overall_score", column_name: "CSAT", type: "call_column" },
    { id: "turn_count", column_name: "Turn Count", type: "call_column" },
    ...(voice
      ? [{ id: "latency", column_name: "Agent Latency (ms)", type: "call_column" }]
      : [{ id: "total_tokens", column_name: "Tokens", type: "call_column" }]),
    { id: "scenario", column_name: "Scenario", type: "call_column" },
    { id: "persona", column_name: "Persona", type: "call_column" },
    ...evalIds.map((id) => ({
      id,
      column_name: resolveEval(id)?.name || id,
      type: "evaluation",
    })),
  ];

  const startedAt = new Date(published?.startedAt || Date.now()).getTime();

  const results = tasks.map((t, i) => {
    const h = hash(t.id);
    const eval_metrics = {};
    (t.evalResults || []).forEach((r) => {
      eval_metrics[r.id] = {
        score: round(r.score, 2),
        value: r.passed ? "pass" : "fail",
        reason: r.reason || "",
      };
    });
    return {
      id: t.id,
      call_execution_id: t.id,
      timestamp: new Date(startedAt + i * 1000).toISOString(),
      status: t.status === "failed" ? "completed" : t.status === "error" ? "failed" : "completed",
      duration: 40 + (h % 120),
      overall_score: Math.max(1, Math.round((t.evalResults?.[0]?.score ?? 0.5) * 10) - 4),
      turn_count: t.steps?.length || 0,
      /* The latency column reads avg_agent_latency, not `latency`. */
      avg_agent_latency: voice ? 300 + (h % 400) : undefined,
      total_tokens: voice ? undefined : 700 + (h % 900),
      scenario: t.title,
      persona: t.persona?.name || "—",
      /* What the Call Details cell renderer reads off the row. */
      type: env?.direction === "inbound" ? "inbound" : "outbound",
      call_type: voice ? "voice" : "chat",
      customer_name: t.persona?.name || "—",
      simulator_agent_name: t.persona?.name || "—",
      agent_definition_used_name: env?.name || "Agent",
      phone_number: voice ? "+1 (415) 555-0182" : undefined,
      provider: "future-agi-sandbox",
      transcript: (t.steps || []).map((step) => ({
        role: step.role === "agent" ? "assistant" : "user",
        content: step.text,
      })),
      eval_metrics,
    };
  });

  const page = Number(params.page || 1);
  const limit = Number(params.limit || 30);
  const search = String(params.search || "").toLowerCase();
  const filtered = search
    ? results.filter((r) => `${r.scenario} ${r.persona}`.toLowerCase().includes(search))
    : results;

  return {
    count: filtered.length,
    status: "completed",
    column_order,
    results: filtered.slice((page - 1) * limit, page * limit),
  };
}

/** One row's call detail — transcript, meta, per-eval scores. */
function callDetailPayload(callId, runId) {
  const { published, env } = runFor(runId || lastRun?.runId);
  const task = (published?.tasks || []).find((t) => t.id === callId);
  const voice = (env?.surface || "chat") === "voice";
  if (!task) return { id: callId, messages: [] };

  return {
    id: callId,
    call_execution_id: callId,
    type: env?.direction === "inbound" ? "inbound" : "outbound",
    status: "completed",
    agent_type: voice ? "voice" : "text",
    provider: "future-agi-sandbox",
    duration: 40 + (hash(callId) % 120),
    turn_count: task.steps?.length || 0,
    scenario: task.title,
    persona: task.persona,
    messages: (task.steps || []).map((s, i) => ({
      id: s.id || `${callId}-${i}`,
      role: s.role === "agent" ? "assistant" : "user",
      content: s.text,
      timestamp: i,
    })),
    eval_metrics: Object.fromEntries(
      (task.evalResults || []).map((r) => [
        r.id,
        { score: round(r.score, 2), value: r.passed ? "pass" : "fail", reason: r.reason || "" },
      ]),
    ),
  };
}

/* ── the adapter ─────────────────────────────────────────────────────────── */

const ok = (data, config) => ({
  data, status: 200, statusText: "OK", headers: {}, config,
});

const isPublishedCall = (callId) =>
  (getPublishedRun(lastRun?.runId)?.tasks || []).some((t) => t.id === callId);

/**
 * Matches the execution URLs and answers them. Returns null when the request
 * is not ours, which is the signal to fall through to the real adapter.
 */
function handle(config) {
  const url = config?.url || "";

  let m = url.match(/test-executions\/([^/]+)\/kpis/);
  if (m && isProtoRun(m[1])) return kpisPayload(m[1]);

  m = url.match(/test-executions\/([^/]+)\/?$/);
  if (m && isProtoRun(m[1])) return listPayload(m[1], config.params || {});

  m = url.match(/run-tests\/([^/]+)\/?$/);
  if (m && isProtoRun(m[1])) return runDetailPayload(m[1]);

  m = url.match(/call-executions\/([^/]+)\/?$/);
  if (m && isPublishedCall(m[1])) return callDetailPayload(m[1]);

  return null;
}

let installed = false;

/*
  Patch the instance, not the package.

  Every request in this app goes through `axios.create()` in utils/axios, and
  an instance resolves its adapter from the defaults it captured when it was
  created — so assigning `axios.defaults.adapter` afterwards reaches nothing.
  That is why the execution screen rendered "undefined Calls analyzed" and a
  grid of ERR: the requests went to the network, which has no such execution.

  Installed at module load rather than on run completion, so the screen also
  works on a reload of its URL, when nothing has re-run to trigger an install.
*/
export function installMockExecutionAdapter() {
  if (installed) return;
  installed = true;

  const wrap = (target) => {
    const real = target.defaults.adapter || axios.getAdapter(axios.defaults.adapter);
    target.defaults.adapter = async (config) => {
      const data = handle(config);
      if (data === null) return axios.getAdapter(real)(config);
      /* A frame of delay, so callers that assume async resolve the same way. */
      await new Promise((r) => setTimeout(r, 60));
      return ok(data, config);
    };
  };

  wrap(axiosInstance);
}

installMockExecutionAdapter();
