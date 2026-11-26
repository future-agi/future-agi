const STOP_CHIPS = {
  requested: "stop_requested",
  confirmed: "stopped",
  unconfirmed: "stop_unconfirmed",
  cleanup_failed: "cleanup_failed",
};

export const STOP_CHIP_STATUSES = new Set(Object.values(STOP_CHIPS));

export const stopChipOf = (stop) => STOP_CHIPS[stop?.status] || null;

const clock = (iso) =>
  iso
    ? new Date(iso).toLocaleTimeString([], {
        hour: "numeric",
        minute: "2-digit",
      })
    : "";

const providerOf = (stop) => stop?.provider || "the provider";

export const stopLiveLineOf = (stop, { voice = false } = {}) => {
  if (!stop) return null;
  const items = voice ? "calls" : "trials";
  const P = providerOf(stop);
  switch (stop.status) {
    case "requested":
      if (stop.kind === "provider")
        return `Stop requested: waiting for ${P} to confirm the calls in progress ended. No new calls start.`;
      if (stop.kind === "endpoint")
        return "Stop requested: we stopped sending and watching. Your service may still finish what it already received, and we can't see it stop.";
      return `Stop requested: ${items} in progress are finishing up. No new ones start.`;
    case "confirmed":
      return stop.kind === "provider"
        ? `Stopped: ${P} confirmed every call ended. Finished calls are kept.`
        : `Stopped: cleanup finished. Finished ${items} are kept.`;
    case "unconfirmed":
      return stop.confirmed_by === "provider_unchecked"
        ? `Stop not confirmed: we couldn't verify with ${P} that the calls ended. Finished calls are kept.`
        : `Stop not confirmed: ${P} still listed a call as open after ${stop.check_window_minutes} minutes. Finished calls are kept.`;
    case "cleanup_failed":
      return `Cleanup failed: the stop wasn't confirmed after ${stop.cleanup_grace_minutes} minutes. Finished ${items} are kept.`;
    default:
      return null;
  }
};

export const stopNoteOf = (stop, { voice = false } = {}) => {
  if (!stop || stop.status === "confirmed") return null;
  const P = providerOf(stop);
  const items = voice ? "calls" : "trials";
  const at = clock(stop.requested_at);
  if (stop.status === "requested" && stop.kind === "endpoint")
    return {
      title: `Stop requested at ${at} · can't be confirmed`,
      body: `This agent runs at its own URL, so we can't see it stop. We stopped sending and watching; your service may still have finished requests it already received. Finished ${items} are kept.`,
    };
  if (stop.status === "requested")
    return {
      title: `Stop requested at ${at} · waiting for confirmation`,
      body:
        stop.kind === "provider"
          ? `Checking with ${P} that every call of this run ended (up to ${stop.check_window_minutes} minutes).`
          : "Waiting for the sandbox to be verified gone.",
    };
  if (
    stop.status === "unconfirmed" &&
    stop.confirmed_by === "provider_unchecked"
  )
    return {
      title: `Stop requested at ${at} · ${P} didn't confirm`,
      body: `We couldn't verify with ${P} that every call of this run ended. An open call keeps running and ${P} charges for it, so check your ${P} dashboard. Finished calls are kept.`,
    };
  if (stop.status === "unconfirmed")
    return {
      title: `Stop requested at ${at} · ${P} didn't confirm`,
      body: `${P} still listed a call on this assistant as open after ${stop.check_window_minutes} minutes. An open call keeps running and ${P} charges for it, so check your ${P} dashboard. Finished calls are kept.`,
    };
  return {
    title: `Stop requested at ${at} · cleanup failed`,
    body:
      stop.kind === "provider"
        ? `The stop wasn't confirmed after ${stop.cleanup_grace_minutes} minutes. A call may still be open on ${P}, and ${P} charges for it, so check your ${P} dashboard. Finished calls are kept.`
        : `The stop wasn't confirmed after ${stop.cleanup_grace_minutes} minutes, so the agent may still be running. Finished ${items} are kept.`,
  };
};

export const stopTipOf = (stop) => {
  if (!stop) return "";
  if (stop.status !== "confirmed") return stopNoteOf(stop)?.title || "";
  const requested = `Stop requested at ${clock(stop.requested_at)}`;
  const confirmed = clock(stop.confirmed_at);
  if (stop.confirmed_by !== "provider_call_ended")
    return `${requested} · sandbox verified gone at ${confirmed}`;
  return stop.kind === "provider"
    ? `${requested} · ${providerOf(stop)} confirmed every call ended at ${confirmed}`
    : `${requested} · ${providerOf(stop)} room verified gone at ${confirmed}`;
};
