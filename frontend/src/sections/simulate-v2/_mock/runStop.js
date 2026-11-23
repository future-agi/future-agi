/*
  A run's Stop, as the agent's side confirms it (TH-8890, PR #3841; plan
  runs.md §6: "Do not show Stopped before confirmation").

  Pressing Stop records when it was requested. It's confirmed only when the
  agent's side is seen to stop:
  - an agent we start in our sandbox: the sandbox is verified gone;
  - a Vapi / Retell agent: the provider reports every call of the run ended
    (checked for about 5 minutes, then left unconfirmed);
  - an agent at its own URL: never — we can't see its service stop.
  A stop still unconfirmed after 10 minutes is recorded as Cleanup failed.

  Demo knob on the live run: ?stop=unconfirmed (the provider check gives up)
  or ?stop=cleanup (cleanup failed).
*/
import { checksConnection, startsFromCode } from "./connectionCheck";

const PROVIDERS = { vapi: "Vapi", retell: "Retell" };

/** "sandbox" | "provider" | "endpoint": how a stop can be confirmed. */
export const stopKindOf = (agent) => {
  const v = agent?.values || {};
  if (v.provider) return "provider";
  if (checksConnection(agent) && !startsFromCode(agent)) return "endpoint";
  return "sandbox";
};

export const providerNameOf = (agent) => {
  const p = agent?.values?.provider;
  return PROVIDERS[p] || (p ? p.charAt(0).toUpperCase() + p.slice(1) : null);
};

/** How long the provider check keeps looking, and when cleanup is failed. */
export const PROVIDER_CHECK_MIN = 5;
export const CLEANUP_FAILED_MIN = 10;

/** The record written when Stop is pressed. */
export const stopRequested = (agent, at = new Date().toISOString()) => ({
  requestedAt: at,
  kind: stopKindOf(agent),
  provider: providerNameOf(agent),
  status: "requested",
});

/** How a requested stop ends, for this kind of agent (and the demo knob). */
export const stopOutcome = (stop, knob, at = new Date().toISOString()) => {
  if (knob === "cleanup")
    return { ...stop, status: "cleanup_failed", failedAt: at };
  if (stop.kind === "endpoint") return stop;
  if (stop.kind === "provider" && knob === "unconfirmed")
    return { ...stop, status: "unconfirmed", gaveUpAt: at };
  return {
    ...stop,
    status: "confirmed",
    confirmedAt: at,
    confirmedBy:
      stop.kind === "provider" ? "provider_call_ended" : "sandbox_absent",
  };
};

/** The run's execution status chip for a stopped run. */
export const stopChipOf = (stop) =>
  ({
    requested: "stop_requested",
    confirmed: "stopped",
    unconfirmed: "stop_unconfirmed",
    cleanup_failed: "cleanup_failed",
  })[stop?.status] || null;

const clock = (iso) =>
  iso
    ? new Date(iso).toLocaleTimeString([], {
        hour: "numeric",
        minute: "2-digit",
      })
    : "";

/** One line for the live run's banner while it stops, and after. */
export const stopLiveLineOf = (stop, { voice = false } = {}) => {
  if (!stop) return null;
  const items = voice ? "calls" : "trials";
  const P = stop.provider;
  switch (stop.status) {
    case "requested":
      if (stop.kind === "provider")
        return `Stop requested: waiting for ${P} to confirm the calls in progress ended. No new calls start.`;
      if (stop.kind === "endpoint")
        return `Stop requested: we stopped sending and watching. Your service may still finish what it already received, and we can't see it stop.`;
      return `Stop requested: ${items} in progress are finishing up. No new ones start.`;
    case "confirmed":
      return stop.kind === "provider"
        ? `Stopped: ${P} confirmed every call ended. Finished calls are kept.`
        : `Stopped: cleanup finished. Finished ${items} are kept.`;
    case "unconfirmed":
      return `Stop not confirmed: ${P} still listed a call as open after ${PROVIDER_CHECK_MIN} minutes. Finished calls are kept.`;
    case "cleanup_failed":
      return `Cleanup failed: the stop wasn't confirmed after ${CLEANUP_FAILED_MIN} minutes. Finished ${items} are kept.`;
    default:
      return null;
  }
};

/**
 * The results page's note for a stop that isn't confirmed: { title, body },
 * or null when it was confirmed (the chip says Stopped).
 */
export const stopNoteOf = (stop) => {
  if (!stop || stop.status === "confirmed") return null;
  const P = stop.provider;
  const at = clock(stop.requestedAt);
  if (stop.status === "requested" && stop.kind === "endpoint")
    return {
      title: `Stop requested at ${at} · can't be confirmed`,
      body: "This agent runs at its own URL, so we can't see it stop. We stopped sending and watching; your service may still have finished requests it already received. Finished trials are kept.",
    };
  if (stop.status === "requested")
    return {
      title: `Stop requested at ${at} · waiting for confirmation`,
      body:
        stop.kind === "provider"
          ? `Checking with ${P} that every call of this run ended (up to ${PROVIDER_CHECK_MIN} minutes).`
          : "Waiting for the sandbox to be verified gone.",
    };
  if (stop.status === "unconfirmed")
    return {
      title: `Stop requested at ${at} · ${P} didn't confirm`,
      body: `${P} still listed a call from this run as open after ${PROVIDER_CHECK_MIN} minutes. An open call keeps running and ${P} charges for it. Check your ${P} dashboard. Finished calls are kept.`,
    };
  return {
    title: `Stop requested at ${at} · cleanup failed`,
    body:
      stop.kind === "provider"
        ? `The stop wasn't confirmed after ${CLEANUP_FAILED_MIN} minutes. A call may still be open on ${P}, and ${P} charges for it. Check your ${P} dashboard. Finished calls are kept.`
        : `The stop wasn't confirmed after ${CLEANUP_FAILED_MIN} minutes, so the agent may still be running. Finished trials are kept.`,
  };
};

/** The chip's hover: when it was requested and how it was confirmed. */
export const stopTipOf = (stop) => {
  if (!stop) return "";
  if (stop.status !== "confirmed") return stopNoteOf(stop)?.title || "";
  return stop.confirmedBy === "provider_call_ended"
    ? `Stop requested at ${clock(stop.requestedAt)} · ${stop.provider} confirmed every call ended at ${clock(stop.confirmedAt)}`
    : `Stop requested at ${clock(stop.requestedAt)} · sandbox verified gone at ${clock(stop.confirmedAt)}`;
};

/** The latest stop recorded on this environment's runs, newest first. */
export const lastStopOf = (envState) =>
  [...(envState?.runs || [])]
    .filter((r) => r.stop?.requestedAt)
    .sort(
      (a, b) => Date.parse(b.stop.requestedAt) - Date.parse(a.stop.requestedAt),
    )
    .map((r) => ({ ...r.stop, runOrdinal: r.ordinal }))[0] || null;
