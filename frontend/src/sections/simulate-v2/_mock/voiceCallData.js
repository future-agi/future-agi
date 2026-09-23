import { localizeEval } from "./errorLocalization";

/**
 * Shape a revamped-flow run task into the `data` object the real
 * observability voice drawer (`VoiceDetailDrawerV2`) reads.
 *
 * The run screen used to open a bespoke call drawer built from scratch. The
 * product already ships one — the same drawer the Observe/Tracing screen uses
 * for a voice call — and the ask is to reuse that component verbatim rather
 * than replicate it. That component is data-driven: give it the `data` shape
 * its panels read (transcript, eval_metrics, call analytics, attributes) and
 * it renders exactly as it does in Observe.
 *
 * This is the one adapter between our in-browser run and that contract. It
 * deliberately marks the call as `module: "simulate"` with no `trace_id` —
 * a simulated call is not a real trace, so the drawer's trace-only actions
 * (tags, share, dataset, open-in-Observe) stay disabled instead of pointing
 * at a backend record that does not exist. `project_id` is omitted for the
 * same reason: it keeps the saved-views/Imagine fetches disabled.
 */

/** Map a run turn's role to the transcript speaker roles the drawer expects. */
const speakerRole = (role) =>
  role === "agent" ? "assistant" : role === "customer" ? "user" : role || "user";

/**
 * Synthesize a tool-call turn to sit before an assistant reply.
 *
 * A simulated call has no captured tool events — the steps only carry
 * text — but tool use is a first-class part of what the agent did, and
 * the reader needs to see it. We infer a plausible tool from the
 * environment's tool list plus a keyword scan of the assistant text
 * ("shipment" → shipment_status, "refund" → issue_refund, etc.) and
 * mint a compact function-call payload the transcript renders in the
 * `tool` speaker style (amber accent). This is Observe-drawer-compatible
 * data (speaker_role: "tool"), not a bespoke new event type.
 *
 * Returns null when no tool intent is detected — most assistant turns
 * are pure conversation, so we don't want a tool row before every one.
 */
const TOOL_INTENT_RULES = [
  { pat: /shipment|carrier|deliver|tracking|arrival|eta|package/i, name: "shipment_status", argsFor: (ctx) => ({ order_id: ctx.orderId }), result: (ctx) => ({ status: "in_transit", eta: ctx.eta || "today 8pm", carrier: "UPS" }) },
  { pat: /refund|reimburs|money\s*back|charge\s*back/i, name: "issue_refund", argsFor: (ctx) => ({ order_id: ctx.orderId, amount_cents: 4999 }), result: () => ({ ok: true, refund_id: "rf_9c3e4a" }) },
  { pat: /return\s*(window|label|policy|eligib)|eligib/i, name: "check_return_eligibility", argsFor: (ctx) => ({ order_id: ctx.orderId }), result: () => ({ eligible: true, window_days: 30, days_remaining: 4 }) },
  { pat: /escalat|supervisor|manager|human\s*agent|transfer/i, name: "escalate_to_human", argsFor: () => ({ queue: "supervisor" }), result: () => ({ queued: true, wait_seconds: 42 }) },
  { pat: /cancel|stop\s*(the\s*)?order/i, name: "cancel_order", argsFor: (ctx) => ({ order_id: ctx.orderId }), result: (ctx) => ({ cancelled: true, cancelled_at: ctx.callTime }) },
  { pat: /return\s*label|print|email\s*(it|the)?\s*(label)?/i, name: "generate_return_label", argsFor: (ctx) => ({ order_id: ctx.orderId }), result: () => ({ url: "https://ship.futureagi.com/labels/rl_1a2b3c.pdf" }) },
  { pat: /order|number|status|placed|invoice/i, name: "lookup_order", argsFor: (ctx) => ({ query: ctx.orderId || "AB-102401" }), result: (ctx) => ({ order_id: ctx.orderId || "AB-102401", status: "shipped", total_cents: 4999 }) },
];

/* Stable hash, so a past call's numbers are the same on every render. */
const hash = (str) => {
  let h = 0;
  for (let i = 0; i < str.length; i += 1) h = ((h << 5) - h + str.charCodeAt(i)) | 0;
  return Math.abs(h);
};

function inferToolCall(assistantText, ctx, allowedTools, key) {
  if (!assistantText) return null;
  const rule = TOOL_INTENT_RULES.find((r) => r.pat.test(assistantText));
  if (!rule) return null;
  if (allowedTools && allowedTools.length && !allowedTools.includes(rule.name)) return null;
  const args = rule.argsFor(ctx);
  const result = rule.result(ctx);
  /* This is a finished call, so the latency is a recorded fact — derived
     from the task + turn, never re-rolled on render. */
  const latencyMs = 80 + (hash(`${key}·${rule.name}`) % 240);
  return { name: rule.name, args, result, latencyMs };
}

/**
 * Render a tool call as its own compact block — a separate transcript row
 * that sits directly under the assistant turn that used it. The drawer's
 * TurnRow gives each row its own bordered container with a speaker-coloured
 * accent, so this reads as a visually distinct card attached to the previous
 * turn rather than a footer stuffed inside its bubble.
 */
function formatToolTurnContent({ name, args, result, latencyMs }) {
  const argsLine = JSON.stringify(args);
  const resultLine = JSON.stringify(result);
  return [
    `🛠 **Function call** · \`${name}\` · ${latencyMs}ms`,
    "```json",
    `→ args: ${argsLine}`,
    `← result: ${resultLine}`,
    "```",
  ].join("\n");
}

/**
 * From a chronological list of run steps, produce a transcript array that
 * interleaves tool-call turns before the assistant replies that used them.
 * Returns entries with the observability-drawer shape (speaker_role: "tool"
 * for tool turns) plus `tool_meta` so downstream views can key off it.
 */
function buildTranscriptWithTools(steps, env, task) {
  const allowedTools = (env?.tools || []).map((t) => t.id || t.name).filter(Boolean);
  // Pick a stable order id per task so every tool call within a call references the same order.
  const orderId = task?.attributes?.order_id
    || (task?.id ? `AB-${(task.id.split("-").pop() || "102401").slice(0, 6).toUpperCase()}` : "AB-102401");
  const ctx = {
    orderId,
    eta: task?.attributes?.eta,
    callTime: task?.finishedAt || task?.startedAt || task?.timestamp || null,
  };

  const out = [];
  let cursor = 0;

  steps.forEach((s, i) => {
    const role = speakerRole(s.role);
    const turnDur = 3;

    out.push({
      id: s.id || `${task?.id || "t"}-t${i}`,
      speaker_role: role,
      role,
      message: s.text,
      content: s.text,
      start_time_seconds: cursor,
      end_time_seconds: cursor + turnDur,
      duration_seconds: turnDur,
    });
    cursor += turnDur;

    /* Tool-call rows follow the assistant reply that used them — a
       separate transcript row so TurnRow renders each in its own
       bordered container (its "own box"), not stuffed into the
       assistant bubble as a footer. Amber accent (the drawer's `tool`
       role color) keeps them visually attached to the turn above. */
    if (role === "assistant") {
      const tool = inferToolCall(s.text, ctx, allowedTools, `${task?.id || "t"}-${i}`);
      if (tool) {
        const dur = Math.max(0.2, tool.latencyMs / 1000);
        out.push({
          id: `${task?.id || "t"}-tool-${i}`,
          speaker_role: "tool",
          role: "tool",
          message: formatToolTurnContent(tool),
          content: formatToolTurnContent(tool),
          start_time_seconds: cursor,
          end_time_seconds: cursor + dur,
          duration_seconds: dur,
          tool_meta: tool,
        });
        cursor += dur;
      }
    }

    cursor += 1; // 1s pause before the next speaker
  });

  return out;
}

/**
 * A stand-in recording for a simulated call.
 *
 * A sim call produced no audio, so the drawer's recording player would show
 * "No recording found". The demo wants the recording UI present, so we
 * synthesise a short, quiet, speech-cadence WAV once and hand the player a
 * blob URL — the same single-track waveform bar a real one-track call gets.
 * Built once and cached; the URL is stable across every call and re-render.
 */
let cachedRecordingUrl = null;
function getMockRecordingUrl() {
  if (cachedRecordingUrl) return cachedRecordingUrl;
  if (typeof window === "undefined" || !window.URL?.createObjectURL) return undefined;

  const sampleRate = 8000;
  const seconds = 12;
  const n = sampleRate * seconds;
  const buffer = new ArrayBuffer(44 + n * 2);
  const view = new DataView(buffer);
  const writeStr = (off, s) => {
    for (let i = 0; i < s.length; i += 1) view.setUint8(off + i, s.charCodeAt(i));
  };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + n * 2, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, "data");
  view.setUint32(40, n * 2, true);

  let off = 44;
  for (let i = 0; i < n; i += 1) {
    const t = i / sampleRate;
    /* Alternating talk/pause blocks so the waveform reads as a conversation
       rather than a flat tone; every 4th ~1.3s block is a silence. */
    const speaking = Math.floor(t / 1.3) % 4 !== 3;
    const env = speaking ? Math.abs(Math.sin(t * 6.3)) * (0.5 + 0.5 * Math.sin(t * 1.7)) : 0;
    const carrier =
      0.6 * Math.sin(2 * Math.PI * 180 * t) +
      0.3 * Math.sin(2 * Math.PI * 320 * t) +
      0.1 * Math.sin(2 * Math.PI * 90 * t);
    const v = Math.max(-1, Math.min(1, env * carrier)) * 0.22; // quiet
    view.setInt16(off, v * 32767, true);
    off += 2;
  }

  try {
    cachedRecordingUrl = window.URL.createObjectURL(new Blob([buffer], { type: "audio/wav" }));
  } catch {
    cachedRecordingUrl = undefined;
  }
  return cachedRecordingUrl;
}

export function taskToVoiceData(task, { env, voice = true } = {}) {
  const steps = task?.steps || [];
  const durationSec = (task?.durationMs || 0) / 1000;
  const direction = env?.direction === "inbound" ? "inbound" : "outbound";

  const agentTurns = steps.filter((s) => s.role === "agent").length;
  const talkAgent = steps.length ? Math.round((agentTurns / steps.length) * 100) : 50;

  /* Each turn becomes a transcript entry, and where an assistant reply
     depended on a tool the tool-call is minted as its own row directly
     before it — same speaker-timeline surface, no separate tab. Timings
     are synthesised at a steady cadence so the analytics view has
     something to plot; a simulated call has no real per-word timing. */
  const transcript = buildTranscriptWithTools(steps, env, task);

  /* Per-eval map keyed by id — the drawer accepts either an array or an
     `{ id: { name, score, reason } }` map and normalises a 0..1 score to a
     0-100 traffic light. */
  const eval_metrics = {};
  (task?.evalResults || []).forEach((r) => {
    eval_metrics[r.id] = {
      id: r.id,
      name: r.name,
      score: r.score,
      value: r.passed ? "pass" : "fail",
      passed: !!r.passed,
      reason: r.reason || "",
      /* Failed evals carry their error localization, so the drawer's Evals
         tab shows the same "Possible Error" card as datasets. */
      ...(localizeEval(task, r) || {}),
    };
  });

  const personaStr = task?.persona
    ? [task.persona.name, task.persona.age, task.persona.voice].filter(Boolean).join(" · ")
    : undefined;

  const summary =
    task?.summary ||
    task?.expected ||
    (task?.title ? `Simulated ${voice ? "call" : "conversation"}: ${task.title}` : "");

  const recordingUrl = voice ? getMockRecordingUrl() : undefined;

  return {
    module: "simulate",
    status: "completed",
    simulation_call_type: voice ? "voice" : "chat",

    id: task?.id,
    call_execution_id: task?.id,
    provider_call_id: task?.id,

    provider: "future-agi-sandbox",
    call_type: direction,
    call_metadata: { provider: "future-agi-sandbox", call_direction: direction },
    duration_seconds: durationSec,
    turn_count: steps.length,
    phone_number: voice ? "+1 (415) 555-0182" : undefined,
    timestamp: task?.finishedAt || task?.startedAt || task?.timestamp || undefined,
    ended_reason: "assistant-ended-call",

    transcript,
    eval_metrics,

    /* Recording — a synthesised stand-in so the player renders the waveform
       bar instead of an empty "No recording found" state. `combined` alone
       routes to the single mixed-track player. */
    recordings: recordingUrl ? { combined: recordingUrl } : undefined,
    audio_url: recordingUrl,

    /* Call Analytics inputs. */
    call_summary: summary,
    agent_talk_percentage: talkAgent,
    talk_ratio: `${talkAgent}/${100 - talkAgent}`,
    avg_agent_latency_ms: voice ? 500 : undefined,
    user_wpm: voice ? 168 : undefined,
    bot_wpm: voice ? 214 : undefined,
    user_interruption_count: 0,
    ai_interruption_count: 0,
    customer_latency_metrics: voice
      ? { system_metrics: { endpointing: 120, transcriber: 90, model: 260, voice: 140 } }
      : undefined,

    /* Attributes tab. */
    attributes: {
      scenario: task?.title,
      persona: personaStr,
      traits: (task?.persona?.traits || []).join(", ") || undefined,
      expected: task?.expected,
      critical: String(!!task?.critical),
    },

    scenario: summary,
  };
}

export default taskToVoiceData;
