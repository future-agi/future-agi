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
let pingTimer = null; // keepalive interval; cleared on close
// conversationId → handler(parsed) — routes inbound frames to the right run.
const handlers = new Map();

// Tear the socket down so the next ensureSocket() builds a fresh one. Used when
// a send lands on a dead-but-readyState-OPEN socket (half-open after a server
// drop) or when the delivery watchdog fires.
function forceReconnect() {
  try {
    if (socket) socket.close();
  } catch {
    /* already closed */
  }
  clearInterval(pingTimer);
  socket = null;
  socketReady = null;
}

// Vite HMR hot-swaps this module on edit, resetting `socket`/`socketReady` to
// null while the *old* socket stays open and orphaned — the engine then holds a
// stale reference and sends into the void (run hangs, no frames on the wire).
// Close it cleanly on dispose so dev gets one live socket, not a split-brain.
if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    forceReconnect();
    handlers.clear();
  });
}

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

    ws.onopen = () => {
      // Keep connection alive — proxies/granian drop idle WebSockets after
      // ~100s, leaving the FE with a half-open socket whose send() succeeds
      // silently into the void (run hangs forever in "streaming"). Mirrors
      // the heartbeat in useFalconSocket.js.
      clearInterval(pingTimer);
      pingTimer = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "ping" }));
        }
      }, 30000);
      resolve(ws);
    };
    ws.onerror = () => reject(new Error("Falcon socket error"));
    ws.onclose = () => {
      clearInterval(pingTimer);
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
  let ws = await ensureSocket(token, workspaceId);
  // The awaited socket can be stale (closed/closing, or a half-open OPEN after a
  // server drop). Rebuild once if it isn't cleanly OPEN so the frame doesn't go
  // into the void.
  if (ws.readyState !== WebSocket.OPEN) {
    forceReconnect();
    ws = await ensureSocket(token, workspaceId);
  }
  ws.send(JSON.stringify(payload));
}

// Open + heartbeat-warm the socket ahead of time (called when the Fix tab
// mounts) so the first run's chat frame lands on a live connection instead of
// a cold/stale one — that cold-socket round-trip was the 20-30s-to-first-paint.
// Idempotent: reuses an already-open socket.
export function prewarmSocket({ token, workspaceId }) {
  if (!token) return;
  ensureSocket(token, workspaceId).catch(() => {});
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
      return args.query
        ? `Searching "${truncate(args.query, 40)}"`
        : "Searching";
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

const CONF_CATEGORY = {
  H: "high confidence",
  M: "medium confidence",
  L: "low confidence",
};

// ── Replay a persisted trail → thread messages ───────────────────────────────

// Rebuild the final thread messages from the agent's persisted rca_trace
// (reasoning / step_start / step_result / synthesis), using the same shaping as
// the live stream. Everything lands "done" — this is a replay, not a stream.
function buildMessagesFromFrames(frames, clusterId) {
  const messages = [];
  const stepIdxByCall = new Map();
  let seq = 0;
  for (const f of frames || []) {
    if (!f || !f.type) continue;
    if (f.type === "reasoning") {
      if (f.text) {
        messages.push({
          id: `h-rsn-${clusterId}-${seq}`,
          type: "reasoning",
          text: f.text,
          instant: true, // replayed from cache — no typewriter
        });
        seq += 1;
      }
    } else if (f.type === "step_start") {
      stepIdxByCall.set(f.call_id, messages.length);
      messages.push({
        id: `h-step-${clusterId}-${f.call_id}`,
        type: "step",
        status: "done",
        title: humanizeStepTitle(f.tool, f.args),
        detail: "",
        chips: [],
        details: [
          {
            kind: "tool",
            name: f.tool,
            input: truncate(f.args, 120),
            output: null,
          },
        ],
      });
    } else if (f.type === "step_result") {
      const idx = stepIdxByCall.get(f.call_id);
      if (idx != null) {
        const { detail, chips } = summarizeResult(f.tool, f.result);
        const m = messages[idx];
        messages[idx] = {
          ...m,
          detail,
          chips,
          details: m.details.map((d) =>
            d.kind === "tool" && d.output == null
              ? { ...d, output: truncate(f.result, 200) }
              : d,
          ),
        };
      }
    } else if (f.type === "synthesis") {
      messages.push({
        id: `h-synth-${clusterId}`,
        type: "synthesis",
        headline: f.synthesis,
        fix: f.fix,
        confidence: f.confidence ?? "M",
        category: "",
        instant: true, // replayed from cache — no typewriter
      });
    }
  }
  return messages;
}

// ── Hydrate from cache ───────────────────────────────────────────────────────

// Seed the thread from the persisted synthesis (rca_*) so an already-analyzed
// cluster shows its result on a fresh load instead of "No analysis yet". Only
// the synthesis is persisted — the reasoning/step trail is live-only, so the
// hydrated thread is marked cachedOnly (no conversationId → no follow-ups until
// a re-run). A live or already-seeded thread always wins.
export function hydrateFromCache({ clusterId, rca }) {
  if (!clusterId || !rca?.synthesis) return;
  const store = useErrorFeedStore.getState();
  if (store.analyzeThreadsByCluster[clusterId]) return;
  const analyzedAtIso = rca.analyzed_at ?? rca.analyzedAt;
  // Replay the full trail if persisted; else fall back to synthesis-only.
  const trail = Array.isArray(rca.trace) ? rca.trace : [];
  const messages = trail.length
    ? buildMessagesFromFrames(trail, clusterId)
    : [
        {
          id: `synth-cached-${clusterId}`,
          type: "synthesis",
          headline: rca.synthesis,
          fix: rca.fix,
          confidence: rca.confidence ?? "M",
          category: "",
          instant: true,
        },
      ];
  store.setAnalyzeThread(clusterId, {
    conversationId: null,
    runState: "done",
    cachedOnly: true,
    startedAt: analyzedAtIso ? new Date(analyzedAtIso).getTime() : null,
    messages,
  });
}

// ── Main run ─────────────────────────────────────────────────────────────────

export async function startRun({ clusterId, projectId, token, workspaceId }) {
  if (!clusterId) return;

  // Guard against duplicate kicks (rapid clicks / StrictMode double-invoke) —
  // a run already in flight for this cluster owns the conversation.
  if (
    useErrorFeedStore.getState().analyzeThreadsByCluster[clusterId]
      ?.runState === "streaming"
  ) {
    return;
  }

  const isRerun =
    (useErrorFeedStore.getState().analyzeThreadsByCluster[clusterId]?.messages
      ?.length ?? 0) > 0;
  const now = new Date();
  const timeLabel = now.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });

  // Flip to streaming IMMEDIATELY (before the conversation-create round-trip +
  // socket connect) so the button/empty-state react the instant it's clicked,
  // instead of sitting idle until the first frame arrives.
  patchThread(clusterId, (t) => ({
    ...t,
    runState: "streaming",
    startedAt: Date.now(),
    conversationId: null,
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

  // Re-run gets a fresh conversation so the BE first-turn guard re-fires.
  let conversation;
  try {
    conversation = await createConversation(
      `Cluster ${clusterId} analysis`,
      "error-feed",
      { hidden: true },
    );
  } catch {
    patchThread(clusterId, (t) => ({ ...t, runState: "idle" }));
    return;
  }
  // Create endpoint wraps the row: { status, result: { id, ... } }.
  const conversationId = conversation?.result?.id ?? conversation?.id;
  if (!conversationId) {
    // No id → the WS chat would go out without conversation_id and the run
    // would hang in the loader. Fail visibly instead.
    patchThread(clusterId, (t) => ({
      ...t,
      runState: "done",
      messages: [
        ...t.messages,
        {
          id: `err-${Date.now()}`,
          type: "synthesis",
          headline:
            "Couldn't start the analysis — the server didn't return a conversation id.",
          fix: "",
          confidence: "L",
          category: "error",
        },
      ],
    }));
    return;
  }
  patchThread(clusterId, (t) => ({ ...t, conversationId }));

  // Per-run scratch state held in the handler closure.
  const openStepByCall = new Map(); // call_id → step message id
  let reasoningSeq = 0;
  let firstFrameSeen = false; // delivery watchdog: did the run actually start?

  const handler = (parsed) => {
    const { type, data } = parsed;
    if (!data) return;
    firstFrameSeen = true; // any frame proves the run actually started

    if (type === "rca_status") {
      // Setup progress ping (before the first LLM round-trip). Held on the
      // thread as a transient status line so the loader shows real activity
      // instead of dead-air; cleared once real frames start arriving.
      patchThread(clusterId, (t) => ({
        ...t,
        status: data.detail || data.phase,
      }));
      return;
    }

    if (type === "rca_reasoning") {
      // The agent's native thinking — its own block in the thread. Rendered
      // instant (no typewriter): the block arrives complete per turn, so a
      // char-by-char animation only lags behind the real stream of turns.
      const text = data.reasoning || data.content;
      if (text) {
        const id = `rsn-${conversationId}-${reasoningSeq}`;
        reasoningSeq += 1;
        patchThread(clusterId, (t) => ({
          ...t,
          status: null,
          messages: [
            ...t.messages,
            { id, type: "reasoning", text, instant: true },
          ],
        }));
      }
      return;
    }

    if (type === "rca_step_start") {
      const stepId = `step-${data.call_id}`;
      openStepByCall.set(data.call_id, stepId);
      const details = [
        {
          kind: "tool",
          name: data.tool,
          input: truncate(data.args, 120),
          output: null,
        },
      ];
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
      const suggestions = Array.isArray(data.suggested_questions)
        ? data.suggested_questions.filter(Boolean)
        : [];
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
          // The agent's grounded "Try asking" set — the compose area reads the
          // latest suggestions message. Omitted when the agent returned none.
          ...(suggestions.length
            ? [
                {
                  id: `sug-${Date.now()}`,
                  type: "suggestions",
                  items: suggestions,
                },
              ]
            : []),
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

  const sendChat = () =>
    send(
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

  // Only act while THIS run still owns the thread — a quick Re-run swaps in a
  // new conversation, and a stale watchdog must not clobber it.
  const isActiveRun = () => {
    const t = useErrorFeedStore.getState().analyzeThreadsByCluster[clusterId];
    return t?.runState === "streaming" && t?.conversationId === conversationId;
  };

  const failVisibly = () => {
    handlers.delete(conversationId);
    patchThread(clusterId, (t) => ({
      ...t,
      runState: "done",
      status: null,
      messages: [
        ...t.messages,
        {
          id: `err-${Date.now()}`,
          type: "synthesis",
          headline:
            "Couldn't reach the investigator — the connection dropped. Hit Re-run.",
          fix: "",
          confidence: "L",
          category: "error",
        },
      ],
    }));
  };

  // Delivery watchdog. Only retry when the socket itself dies (readyState flips
  // to CLOSED/CLOSING) before the first frame — NOT on mere silence, because a
  // slow-but-healthy backend (cold gateway, queued worker) already received the
  // chat and the backend deliberately survives disconnects, so resending would
  // double-run the agent.
  let retried = false;
  const armWatchdog = () => {
    setTimeout(async () => {
      if (firstFrameSeen || !isActiveRun()) return;
      // Only retry if the socket is actually dead.
      const ws = socket;
      const socketAlive =
        ws &&
        (ws.readyState === WebSocket.OPEN ||
          ws.readyState === WebSocket.CONNECTING);
      if (socketAlive) {
        // Socket is fine — backend is just slow. Re-arm to check again, but
        // never resend.
        armWatchdog();
        return;
      }
      if (retried) {
        failVisibly();
        return;
      }
      retried = true;
      forceReconnect();
      try {
        await sendChat();
        armWatchdog();
      } catch {
        failVisibly();
      }
    }, 8000);
  };

  try {
    await sendChat();
    armWatchdog();
  } catch {
    handlers.delete(conversationId);
    patchThread(clusterId, (t) => ({ ...t, runState: "done" }));
  }
}

// ── Follow-up (Falcon takes over — plain chat, no skill) ─────────────────────

export async function runFollowUp({
  clusterId,
  question,
  projectId,
  token,
  workspaceId,
}) {
  const text = String(question ?? "").trim();
  if (!clusterId || !text) return;

  const thread =
    useErrorFeedStore.getState().analyzeThreadsByCluster[clusterId];
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
      {
        id: `${baseId}-i`,
        type: "assistant_intro",
        text: "Let me look into that.",
      },
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
                    ? {
                        ...s,
                        status: "done",
                        detail: truncate(data.result_summary, 90),
                      }
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
            ? {
                ...m,
                status: "done",
                answer: answer || data.error || "Something went wrong.",
              }
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
