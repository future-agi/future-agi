// Real Falcon-WebSocket engine for the Analyze tab.
//
// The cluster-RCA agent is a Falcon skill: a run is just a Falcon chat that
// activates `/cluster-rca` on a fresh conversation. The dedicated agent streams
// its investigation back over this socket as `rca_*` events; once it finishes,
// Falcon answers follow-ups (plain chat, no skill) on the SAME conversation with
// the synthesis already in context.
//
// This lives at module scope (not in a component) so a run keeps progressing —
// and keeps patching the store — even when the Analyze tab is unmounted. Both
// the headline card and the tab observe the same per-cluster thread.
//
// State is intentionally ephemeral: the Falcon conversation isn't persisted on
// the client. A page reload starts fresh (the synthesis is cached server-side on
// the cluster, so the headline still shows the last result).

import { HOST_API } from "src/config-global";
import { createConversation } from "src/sections/falcon-ai/hooks/useFalconAPI";
import { useErrorFeedStore } from "./store";

// ── Connection (single shared socket) ──────────────────────────────────────

let socket = null;
let socketReady = null; // Promise<WebSocket> while connecting/open
// conversationId → handler(parsed) — routes inbound frames to the right run.
const handlers = new Map();

function buildWsUrl(token, workspaceId) {
  const isSecure = HOST_API.includes("https");
  const wsHost = HOST_API.replace(/^https?:\/\//, "").replace(/\/+$/, "");
  const protocol = isSecure ? "wss" : "ws";
  const params = new URLSearchParams({ token });
  if (workspaceId) params.set("workspace_id", workspaceId);
  return `${protocol}://${wsHost}/ws/falcon-ai/?${params.toString()}`;
}

function ensureSocket(token, workspaceId) {
  if (
    socket &&
    (socket.readyState === WebSocket.OPEN ||
      socket.readyState === WebSocket.CONNECTING) &&
    socketReady
  ) {
    return socketReady;
  }

  socketReady = new Promise((resolve, reject) => {
    const ws = new WebSocket(buildWsUrl(token, workspaceId));
    socket = ws;

    ws.onopen = () => resolve(ws);
    ws.onerror = () => reject(new Error("Falcon socket error"));
    ws.onclose = () => {
      if (socket === ws) {
        socket = null;
        socketReady = null;
      }
    };
    ws.onmessage = (event) => {
      let parsed;
      try {
        parsed = JSON.parse(event.data);
      } catch {
        return;
      }
      const convId = parsed?.data?.conversation_id;
      if (convId && handlers.has(convId)) {
        handlers.get(convId)(parsed);
      }
    };
  });
  return socketReady;
}

async function send(payload, token, workspaceId) {
  const ws = await ensureSocket(token, workspaceId);
  ws.send(JSON.stringify(payload));
}

// ── Store helpers ───────────────────────────────────────────────────────────

function patchThread(clusterId, mutator) {
  const store = useErrorFeedStore.getState();
  const current = store.analyzeThreadsByCluster[clusterId];
  const seed = current ?? {
    messages: [],
    runState: "idle",
    startedAt: null,
    conversationId: null,
  };
  store.setAnalyzeThread(clusterId, mutator(seed));
}

// ── Humanizers (tool call + result → readable step) ──────────────────────────

const TOOL_TITLES = {
  list: "Listing",
  search: "Searching",
  read: "Reading",
  aggregate: "Grouping",
  compare: "Comparing populations",
  timeline: "Checking failure timeline",
  submit_finding: "Recording a finding",
  submit_synthesis: "Synthesizing",
};

function humanizeStepTitle(tool, args = {}) {
  switch (tool) {
    case "list":
      return `Listing ${args.dimension ?? "items"}`;
    case "read":
      return `Reading ${args.entity ?? "entity"}`;
    case "aggregate":
      return `Grouping by ${args.group_by ?? args.metric ?? "dimension"}`;
    case "search":
      return args.query ? `Searching "${truncate(args.query, 40)}"` : "Searching";
    default:
      return TOOL_TITLES[tool] ?? tool;
  }
}

function truncate(v, n) {
  const s = typeof v === "string" ? v : JSON.stringify(v ?? "");
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

// Best-effort one-line summary + chips from a tool result. Result shapes vary
// by tool; stay defensive — never throw on an unexpected shape.
function summarizeResult(tool, result) {
  if (result == null || typeof result !== "object") {
    return { detail: truncate(result, 90), chips: [] };
  }
  try {
    if (Array.isArray(result.buckets)) {
      const top = result.buckets[0];
      const chips = result.buckets
        .slice(0, 3)
        .map((b) => `${truncate(b.key, 18)} ${Math.round(b.pct ?? 0)}%`);
      const detail = top
        ? `${result.buckets.length} groups · top ${truncate(top.key, 24)} (${Math.round(top.pct ?? 0)}%)`
        : "no groups";
      return { detail, chips };
    }
    if (Array.isArray(result.items)) {
      return {
        detail: `${result.total_count ?? result.items.length} results`,
        chips: [],
      };
    }
    if (result.set_a_count != null) {
      return {
        detail: `A:${result.set_a_count} · B:${result.set_b_count} · ∩${result.intersection_count}`,
        chips: result.lift_a_over_b ? [`lift ${result.lift_a_over_b}×`] : [],
      };
    }
    if (Array.isArray(result.points)) {
      return { detail: `${result.points.length} time buckets`, chips: [] };
    }
    if (result.error) {
      return { detail: truncate(result.error, 90), chips: ["error"] };
    }
    // Generic object — show its keys.
    return { detail: truncate(Object.keys(result).join(", "), 90), chips: [] };
  } catch {
    return { detail: "", chips: [] };
  }
}

const CONF_CATEGORY = { H: "high confidence", M: "medium confidence", L: "low confidence" };

// ── Main run ─────────────────────────────────────────────────────────────────

export async function startRun({ clusterId, projectId, token, workspaceId }) {
  if (!clusterId) return;

  const isRerun =
    (useErrorFeedStore.getState().analyzeThreadsByCluster[clusterId]?.messages
      ?.length ?? 0) > 0;
  const now = new Date();
  const timeLabel = now.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });

  // Re-run gets a fresh conversation so the BE first-turn guard re-fires.
  let conversation;
  try {
    conversation = await createConversation(`Cluster ${clusterId} analysis`, "error-feed");
  } catch {
    patchThread(clusterId, (t) => ({ ...t, runState: "idle" }));
    return;
  }
  const conversationId = conversation?.id;

  patchThread(clusterId, (t) => ({
    ...t,
    conversationId,
    runState: "streaming",
    startedAt: Date.now(),
    messages: isRerun
      ? [
          ...t.messages,
          {
            id: `hdr-${Date.now()}`,
            type: "run_header",
            label: "Re-run",
            timestamp: timeLabel,
          },
        ]
      : [],
  }));

  // Per-run scratch state held in the handler closure.
  let pendingReasoning = null; // {kind:'reasoning', text} awaiting its step
  const openStepByCall = new Map(); // call_id → step message id

  const handler = (parsed) => {
    const { type, data } = parsed;
    if (!data) return;

    if (type === "rca_reasoning") {
      const text = data.reasoning || data.content;
      if (text) pendingReasoning = { kind: "reasoning", text };
      return;
    }

    if (type === "rca_step_start") {
      const stepId = `step-${data.call_id}`;
      openStepByCall.set(data.call_id, stepId);
      const details = [];
      if (pendingReasoning) {
        details.push(pendingReasoning);
        pendingReasoning = null;
      }
      details.push({
        kind: "tool",
        name: data.tool,
        input: truncate(data.args, 120),
        output: null,
      });
      patchThread(clusterId, (t) => ({
        ...t,
        messages: [
          ...t.messages,
          {
            id: stepId,
            type: "step",
            status: "running",
            title: humanizeStepTitle(data.tool, data.args),
            detail: "",
            chips: [],
            details,
          },
        ],
      }));
      return;
    }

    if (type === "rca_step_result") {
      const stepId = openStepByCall.get(data.call_id);
      if (!stepId) return;
      const { detail, chips } = summarizeResult(data.tool, data.result);
      patchThread(clusterId, (t) => ({
        ...t,
        messages: t.messages.map((m) =>
          m.id === stepId
            ? {
                ...m,
                status: "done",
                detail,
                chips,
                details: m.details.map((d) =>
                  d.kind === "tool" && d.output == null
                    ? { ...d, output: truncate(data.result, 200) }
                    : d,
                ),
              }
            : m,
        ),
      }));
      openStepByCall.delete(data.call_id);
      return;
    }

    if (type === "rca_synthesis") {
      patchThread(clusterId, (t) => ({
        ...t,
        messages: [
          ...t.messages,
          {
            id: `synth-${Date.now()}`,
            type: "synthesis",
            headline: data.synthesis,
            fix: data.fix,
            confidence: data.confidence,
            category: CONF_CATEGORY[data.confidence] ?? "",
          },
        ],
      }));
      return;
    }

    if (type === "done") {
      patchThread(clusterId, (t) => ({ ...t, runState: "done" }));
      handlers.delete(conversationId);
      return;
    }

    if (type === "error") {
      patchThread(clusterId, (t) => ({
        ...t,
        runState: "done",
        messages: [
          ...t.messages,
          {
            id: `err-${Date.now()}`,
            type: "synthesis",
            headline: data.error || "The analysis run failed.",
            fix: "",
            confidence: "L",
            category: "error",
          },
        ],
      }));
      handlers.delete(conversationId);
    }
  };

  handlers.set(conversationId, handler);

  try {
    await send(
      {
        type: "chat",
        message: "/cluster-rca",
        conversation_id: conversationId,
        context: {
          page: "error-feed",
          entity_type: "error_cluster",
          entity_id: clusterId,
          project_id: projectId,
        },
      },
      token,
      workspaceId,
    );
  } catch {
    handlers.delete(conversationId);
    patchThread(clusterId, (t) => ({ ...t, runState: "done" }));
  }
}

// ── Follow-up (Falcon takes over — plain chat, no skill) ─────────────────────

export async function runFollowUp({ clusterId, question, projectId, token, workspaceId }) {
  const text = String(question ?? "").trim();
  if (!clusterId || !text) return;

  const thread = useErrorFeedStore.getState().analyzeThreadsByCluster[clusterId];
  const conversationId = thread?.conversationId;
  if (!conversationId) return; // no run yet — nothing to follow up on

  const baseId = `fu-${Date.now()}`;
  const subagentMsgId = `${baseId}-sa`;

  patchThread(clusterId, (t) => ({
    ...t,
    followUpRunState: "streaming",
    messages: [
      ...t.messages,
      { id: `${baseId}-q`, type: "user_question", text },
      { id: `${baseId}-i`, type: "assistant_intro", text: "Let me look into that." },
      {
        id: subagentMsgId,
        type: "subagent",
        title: "Falcon",
        status: "streaming",
        steps: [],
        answer: null,
      },
    ],
  }));

  let answer = "";
  const openCalls = new Map(); // call_id → step id

  const handler = (parsed) => {
    const { type, data } = parsed;
    if (!data) return;

    if (type === "tool_call_start") {
      const stepId = `${subagentMsgId}-${data.call_id}`;
      openCalls.set(data.call_id, stepId);
      patchThread(clusterId, (t) => ({
        ...t,
        messages: t.messages.map((m) =>
          m.id === subagentMsgId
            ? {
                ...m,
                steps: [
                  ...m.steps,
                  {
                    id: stepId,
                    title: data.tool_description || data.tool_name,
                    detail: "",
                    status: "running",
                  },
                ],
              }
            : m,
        ),
      }));
      return;
    }

    if (type === "tool_call_result") {
      const stepId = openCalls.get(data.call_id);
      patchThread(clusterId, (t) => ({
        ...t,
        messages: t.messages.map((m) =>
          m.id === subagentMsgId
            ? {
                ...m,
                steps: m.steps.map((s) =>
                  s.id === stepId
                    ? { ...s, status: "done", detail: truncate(data.result_summary, 90) }
                    : s,
                ),
              }
            : m,
        ),
      }));
      openCalls.delete(data.call_id);
      return;
    }

    if (type === "text_delta") {
      answer += data.delta || "";
      patchThread(clusterId, (t) => ({
        ...t,
        messages: t.messages.map((m) =>
          m.id === subagentMsgId ? { ...m, answer } : m,
        ),
      }));
      return;
    }

    if (type === "done") {
      patchThread(clusterId, (t) => ({
        ...t,
        followUpRunState: "done",
        messages: t.messages.map((m) =>
          m.id === subagentMsgId ? { ...m, status: "done" } : m,
        ),
      }));
      handlers.delete(conversationId);
      return;
    }

    if (type === "error") {
      patchThread(clusterId, (t) => ({
        ...t,
        followUpRunState: "done",
        messages: t.messages.map((m) =>
          m.id === subagentMsgId
            ? { ...m, status: "done", answer: answer || data.error || "Something went wrong." }
            : m,
        ),
      }));
      handlers.delete(conversationId);
    }
  };

  handlers.set(conversationId, handler);

  try {
    await send(
      {
        type: "chat",
        message: text,
        conversation_id: conversationId,
        context: {
          page: "error-feed",
          entity_type: "error_cluster",
          entity_id: clusterId,
          project_id: projectId,
        },
      },
      token,
      workspaceId,
    );
  } catch {
    handlers.delete(conversationId);
    patchThread(clusterId, (t) => ({ ...t, followUpRunState: "done" }));
  }
}
