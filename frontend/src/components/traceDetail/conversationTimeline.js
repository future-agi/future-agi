import {
  readSpanAttachments,
  spanParent,
} from "../attachments/attachmentPlacement";
import {
  normalizeRole,
  getContent,
  formatClock,
} from "../VoiceDetailDrawerV2/transcriptUtils";

// Discovery uses the raw tree. A projected child always keeps its own resolver parent.
export function discoverRecordings(tree, selected, root) {
  const result = new Map();
  const visit = (node, inherited) => {
    const owner = node?.observation_span;
    if (!owner || owner.trace !== selected?.trace) return;
    const matches = owner.id === selected.id;
    const include = matches || inherited;
    if (include) {
      const parent = spanParent(owner);
      for (const item of parent ? readSpanAttachments(owner) : []) {
        const ref = item.reference;
        if (
          ref.purpose !== "conversation_recording" ||
          ref.modality !== "audio"
        )
          continue;
        const key = `${parent.trace_id}/${parent.span_id}/${item.recording_id || ref.occurrence_id}`;
        if (!result.has(key))
          result.set(key, { key, owner: node, parent, item });
      }
    }
    node.children?.forEach((child) => visit(child, root && include));
  };
  tree?.forEach((node) => visit(node, false));
  return [...result.values()];
}
const finite = (n) => typeof n === "number" && Number.isFinite(n);
const wall = (t) =>
  typeof t === "string" && t.trim() && Number.isFinite(Date.parse(t))
    ? Date.parse(t)
    : null;
const scalar = (v) => (typeof v === "string" && v.trim() ? v : null);
function transcript(value) {
  if (typeof value === "string" && value.length <= 131072) {
    try {
      value = JSON.parse(value);
    } catch {
      return [];
    }
  }
  return Array.isArray(value)
    ? value.slice(0, 2000).filter((t) => t && typeof t === "object")
    : [];
}
export function clock(ms) {
  const tenths = Math.floor((ms % 1000) / 100);
  return formatClock(ms / 1000) + (tenths ? `.${tenths}` : "");
}
const roleLabel = (r) =>
  ({
    assistant: "Agent",
    user: "User",
    tool: "Tool",
    system: "System",
    unknown: "Unknown speaker",
  })[r] || r;
export function conversationTimeline(
  { owner, item, ambiguous = false },
  manifest,
) {
  const rows = [],
    coveredSpans = new Set(),
    coveredTurns = new Set(),
    tools = new Set();
  const span = owner.observation_span,
    origin = wall(span.start_time);
  const segments = new Map();
  for (const seg of manifest?.segments ?? [])
    if (!segments.has(seg.occurrence_id))
      segments.set(seg.occurrence_id, seg.start_ms);
  const association = (source) =>
    !ambiguous ||
    (item.recording_id && source.recording_id === item.recording_id) ||
    (item.reference.conversation_id &&
      source.conversation_id === item.reference.conversation_id);
  function add(kind, actor, text, spanId, exact, wallMs, durationMs) {
    const traceMs = origin !== null && finite(wallMs) ? wallMs - origin : null;
    // No verified recording origin is provided: wall time is always trace time.
    const sortMs = finite(exact) ? exact : traceMs;
    const clockLabel = finite(exact)
      ? "Recording time"
      : finite(traceMs)
        ? "Trace time"
        : "Time not captured";
    rows.push({
      key: `${spanId}:${rows.length}`,
      kind,
      actor,
      text,
      spanId,
      sortMs,
      clockLabel,
      seekMs:
        (finite(exact) || !ambiguous) &&
        finite(sortMs) &&
        sortMs >= 0 &&
        sortMs < manifest?.duration_ms
          ? sortMs
          : null,
      durationMs:
        finite(durationMs) && durationMs >= 0 ? durationMs : undefined,
    });
  }
  for (const turn of transcript(
    span.span_attributes?.["fi.conversation.transcript"],
  )) {
    if (!association(turn)) continue;
    const role = normalizeRole(
      turn.speaker_role || turn.role || turn.actor_role,
    );
    const actor =
      scalar(turn.speaker_name) || scalar(turn.actor_id) || roleLabel(role);
    const exact = [
      turn.recording_offset_ms,
      turn.start_ms,
      finite(turn.start_time_seconds) ? 1000 * turn.start_time_seconds : null,
      finite(turn.startTimeSeconds) ? 1000 * turn.startTimeSeconds : null,
      segments.get(turn.occurrence_id),
    ].find(finite);
    const text = getContent(turn);
    if (text) {
      add(
        role === "tool" ? "Tool" : "Turn",
        actor,
        text,
        turn.span_id || span.id,
        exact,
        wall(turn.timestamp),
      );
      if (turn.span_id) coveredSpans.add(turn.span_id);
      if (turn.turn_id) coveredTurns.add(turn.turn_id);
    }
    for (const call of Array.isArray(turn.tool_calls) ? turn.tool_calls : []) {
      if (!call || typeof call !== "object") continue;
      if (call.id) tools.add(call.id);
      add(
        "Tool",
        "Tool",
        scalar(call.function?.name) || scalar(call.name) || "Tool call",
        call.span_id || span.id,
        call.start_ms,
        wall(call.timestamp),
        call.duration_ms,
      );
    }
  }
  const hasTranscript = rows.some((row) => row.kind === "Turn");
  function walk(node, first, anchored = false) {
    const s = node.observation_span;
    if (!s || s.trace !== span.trace) return;
    const a = s.span_attributes || {};
    // A nested independently recorded call has its own timeline.
    if (
      !first &&
      readSpanAttachments(s).some(
        (x) =>
          x.reference.modality === "audio" &&
          x.reference.purpose === "conversation_recording",
      )
    )
      return;
    const include = anchored || association(a);
    if (include && !first) {
      const type = String(s.observation_type || "Activity").toLowerCase();
      const name = scalar(s.name) || "Unnamed activity";
      const turn = ["user_turn", "agent_turn"].includes(name.toLowerCase());
      const actor =
        scalar(a["gen_ai.agent.name"]) || scalar(a["lk.agent_name"]);
      const kind =
        {
          generation: "LLM",
          llm: "LLM",
          tool: "Tool",
          agent: "Agent",
          retriever: "Retrieval",
          embedding: "Embedding",
        }[type] || type;
      const dup =
        coveredSpans.has(s.id) || (a.turn_id && coveredTurns.has(a.turn_id));
      if (!(turn && dup) && !(kind === "Tool" && tools.has(a.tool_call_id))) {
        const speech = turn && !hasTranscript;
        const label = speech
          ? actor || (name.toLowerCase() === "user_turn" ? "User" : "Agent")
          : actor || kind;
        add(
          speech ? "Turn" : kind,
          label,
          speech
            ? scalar(a["fi.turn.text"]) ||
                scalar(s.output) ||
                scalar(s.input) ||
                name
            : name,
          s.id,
          null,
          wall(s.start_time),
          s.latency_ms,
        );
      }
    }
    if (include)
      for (const e of Array.isArray(s.span_events) ? s.span_events : [])
        if (e?.name)
          add("Event", "Event", String(e.name), s.id, null, wall(e.timestamp));
    for (const child of node.children || []) walk(child, false, include);
  }
  walk(owner, true);
  return rows.sort((a, b) => (a.sortMs ?? Infinity) - (b.sortMs ?? Infinity));
}
