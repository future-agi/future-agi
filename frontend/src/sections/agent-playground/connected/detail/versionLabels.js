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

// Where a pending check stands, when the version says.
export const STEP_LABEL = {
  snapshot: "Storing the code of",
  reading: "Reading",
  check: "Checking",
};

export const checkLabel = (v) => {
  if (v.check === "pending" && v.checkStep === "reading") return "Reading";
  if (v.check === "pending" && v.checkStep === "snapshot")
    return "Storing code";
  if (v.checkFailure?.code === "superseded") return "Superseded";
  return CHECK_LABEL[v.check] ?? null;
};

export const environmentsLabel = (count) =>
  count ? `Running in ${count} environment${count === 1 ? "" : "s"}` : null;

// Whether a checked version may be made production, retried or discarded.
export const canPromote = (v) =>
  !v.isActive && !["pending", "failed", "discarded"].includes(v.check);
export const canRetry = (v) =>
  v.check === "failed" && v.checkFailure?.retryable !== false;
export const canDiscard = (v) =>
  !v.isActive && ["pending", "failed"].includes(v.check);

// What a failed reading means, in the page's words.
export const FAILURE_TEXT = {
  agent_type_unsupported:
    "This kind of agent isn't supported yet (browser, computer-use and crawler agents).",
  subfolder_not_found: "The folder named isn't in the repository.",
  channel_changed:
    "This version changed from chat to voice or back; connect it as a new agent.",
  agent_reading_unavailable: "The reading couldn't run. Try again in a moment.",
};

export const failureText = (failure) => {
  if (!failure) return "";
  return FAILURE_TEXT[failure.code] || failure.message || failure.code;
};
