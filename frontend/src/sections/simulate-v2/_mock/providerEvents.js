/*
  Provider events (roadmap platforms.md "Webhook delivery"): what Vapi /
  Retell sent about one call, normalized, with when it arrived and the trial
  it belongs to. A webhook delivered twice is matched to the same call — it
  updates that trial, never starts a second one or charges twice.
*/
import { hashSeed } from "./runStream";

const label = (p) => (p ? p.charAt(0).toUpperCase() + p.slice(1) : "The platform");

/** [{ at (s from start), type, line, duplicate? }] for one trial, plus its provider call id. */
export const providerEventsOf = (task, provider) => {
  const h = hashSeed(String(task?.id || "call"));
  const callId = `call_${(h >>> 0).toString(36).slice(0, 8)}`;
  const steps = (task?.steps || []).length || 6;
  const tools = (task?.steps || []).filter((s) => s.tool || s.role === "tool").map((s) => s.tool || s.name).filter(Boolean);
  const P = label(provider);
  let t = 0;
  const ev = [{ at: t, type: "call.started", line: `${P} started the call and gave it ID ${callId}` }];
  t += 2;
  ev.push({ at: t, type: "transcript", line: `Transcript streaming — ${steps} turns over the call` });
  (tools.length ? tools : ["check_availability"]).slice(0, 3).forEach((name, i) => {
    t += 6 + i * 4;
    ev.push({ at: t, type: "tool.called", line: `${name} — redirected to the test world, answered in ${120 + ((h >> i) % 200)} ms` });
  });
  t += 9;
  ev.push({ at: t, type: "call.ended", line: `${P} reported the call ended (${task?.status === "unmeasured" ? "connection dropped" : "assistant hung up"})` });
  t += 1;
  ev.push({ at: t, type: "webhook", line: `end-of-call report received — matched to this trial by ${callId}` });
  /* Every few calls the platform redelivers its webhook. */
  if (h % 3 === 0)
    ev.push({ at: t + 4, type: "webhook", duplicate: true, line: `end-of-call report received again — same ${callId}, so it updated this trial; no second trial, no second charge` });
  return { callId, events: ev };
};
