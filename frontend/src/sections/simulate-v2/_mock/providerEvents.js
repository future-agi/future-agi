/*
  Provider events (roadmap platforms.md "Webhook delivery"): what Vapi /
  Retell sent about one call, normalized, with when it arrived and the trial
  it belongs to. A webhook delivered twice is matched to the same call — it
  updates that trial, never starts a second one or charges twice.
  With the call record (taskToVoiceData) the events follow that call: its
  tool calls at their times, its length and how it ended.
*/
import { hashSeed } from "./runStream";
import { callToolsOf } from "./callRecord";

const label = (p) =>
  p ? p.charAt(0).toUpperCase() + p.slice(1) : "The platform";

/** [{ at (s from start), type, line, duplicate? }] for one trial, plus its provider call id. */
export const providerEventsOf = (task, provider, call = null) => {
  /* Unsigned, so the timings below can never go negative. */
  const h = hashSeed(String(task?.trialId || task?.id || "call")) >>> 0;
  const callId = `call_${h.toString(36).slice(0, 8)}`;
  const P = label(provider);
  const speech = (call?.transcript || []).filter(
    (r) => !r.tool_meta && !r.actor_event,
  ).length;
  const turns = speech || (task?.steps || []).length || 6;
  const end = Math.round(call?.duration_seconds || 0) || null;
  const ev = [
    {
      at: 0,
      type: "call.started",
      line: `${P} started the call and gave it ID ${callId}`,
    },
  ];
  ev.push({
    at: 2,
    type: "transcript",
    line: `Transcript streaming — ${turns} turns over the call`,
  });
  if (call) {
    callToolsOf(call.transcript).forEach((t) =>
      ev.push({
        at: Math.round(t.at),
        type: "tool.called",
        line: `${t.name}${t.worker ? ` (${t.worker})` : ""} — redirected to the test world, answered in ${t.latencyMs} ms`,
      }),
    );
  } else {
    const tools = (task?.steps || [])
      .filter((s) => s.tool || s.role === "tool")
      .map((s) => s.tool || s.name)
      .filter(Boolean);
    let t = 2;
    tools.slice(0, 3).forEach((name, i) => {
      t += 6 + i * 4;
      ev.push({
        at: t,
        type: "tool.called",
        line: `${name} — redirected to the test world, answered in ${120 + ((h >>> i) % 200)} ms`,
      });
    });
  }
  const endAt = end ?? Math.max(...ev.map((e) => e.at)) + 9;
  const how = call?.ended_reason
    ? call.ended_reason.charAt(0).toLowerCase() + call.ended_reason.slice(1)
    : task?.status === "unmeasured"
      ? "connection dropped"
      : "assistant hung up";
  ev.push({
    at: endAt,
    type: "call.ended",
    line: `${P} reported the call ended (${how})`,
  });
  ev.push({
    at: endAt + 1,
    type: "webhook",
    line: `end-of-call report received — matched to this trial by ${callId}`,
  });
  /* Every few calls the platform redelivers its webhook. */
  if (h % 3 === 0)
    ev.push({
      at: endAt + 5,
      type: "webhook",
      duplicate: true,
      line: `end-of-call report received again — same ${callId}, so it updated this trial; no second trial, no second charge`,
    });
  return { callId, events: ev.sort((a, b) => a.at - b.at) };
};
