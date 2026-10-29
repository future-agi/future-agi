// How a version came to be, from its side row's trigger.
export const ORIGIN_LABEL = {
  connect: "Connected",
  creation: "Built with an environment",
  sync: "Synced",
  push: "Pushed",
  upload: "Uploaded",
  prompt: "Prompt edited",
  agent_payload: "Settings changed",
  credentials: "Credentials changed",
  provider_sync: "Provider synced",
  backfill: "Recorded",
};

// The check a new version goes through before it can run; a version from
// before versioning has none and reads as checked.
export const CHECK_LABEL = {
  pending: "Checking",
  failed: "Check failed",
  discarded: "Discarded",
};

export const environmentsLabel = (count) =>
  count ? `Running in ${count} environment${count === 1 ? "" : "s"}` : null;

// Whether a checked version may be made production, retried or discarded.
export const canPromote = (v) =>
  !v.isActive && !["pending", "failed", "discarded"].includes(v.check);
export const canRetry = (v) => v.check === "failed";
export const canDiscard = (v) =>
  !v.isActive && ["pending", "failed"].includes(v.check);
