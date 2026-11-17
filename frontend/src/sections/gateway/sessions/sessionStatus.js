import { EMPTY } from "../utils/formatters";

// A session's status as the backend reads it (agentcc/services/
// session_activity.py): a session closes after its idle timeout passes with
// no request (closed_reason "idle"), or when someone closes it ("closed"); a
// new request with an idle session's id opens it again, while one a user
// closed stays closed: that one reads "Closed for good". Status stays on the
// neutral chip, never a green health claim. A value this file does not know
// is shown as sent.
const LABELS = { active: "Active", closed: "Closed" };
const CLOSED_FOR_GOOD = "Closed for good";

/** "30 minutes", "1 hour", "90 seconds": the idle timeout in words. */
export function idleTimeoutLabel(seconds) {
  const n = Number(seconds);
  if (!Number.isFinite(n) || n <= 0) return "";
  const unit = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;
  if (n % 3600 === 0) return unit(n / 3600, "hour");
  if (n % 60 === 0) return unit(n / 60, "minute");
  return unit(n, "second");
}

/** What the Status header says: when a session closes. */
export function sessionStatusHelp(idleTimeoutSeconds) {
  const after = idleTimeoutLabel(idleTimeoutSeconds) || "its idle timeout";
  return `A session closes after ${after} with no request, or when someone closes it. A new request with its id opens an idle session again.`;
}

export function sessionStatus(status, closedReason, idleTimeoutSeconds) {
  let detail = "";
  if (closedReason === "idle") {
    const idle = idleTimeoutLabel(idleTimeoutSeconds) || "its idle timeout";
    detail = `No request for ${idle}. A new request with this session id opens it again.`;
  } else if (closedReason === "closed") {
    // The console's Close and the API's close both set it.
    return {
      tone: "neutral",
      label: CLOSED_FOR_GOOD,
      detail:
        "Closed by a user. It stays closed when a request with this session id arrives.",
    };
  }
  return { tone: "neutral", label: LABELS[status] || status || EMPTY, detail };
}

/** A session's own name, or "" when it is only its id (auto-created). */
export function ownName(session) {
  return session?.name && session.name !== session.session_id
    ? session.name
    : "";
}

/**
 * When a session started: its first request (`first_request_at`), or, for a
 * session with no request yet, when the gateway stored it (`created_at`, one
 * time for a whole ingestion batch).
 */
export function sessionStartedAt(session) {
  return session?.first_request_at || session?.created_at || null;
}
