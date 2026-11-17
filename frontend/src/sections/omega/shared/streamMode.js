/**
 * How this document reads Omega streams (READINESS CH-12).
 *
 * "stream" is plain Server-Sent Events. "poll" is a run of long-poll windows,
 * for a browser behind a proxy that holds a response until it completes (a
 * TLS-inspecting corporate proxy). Once a stream shows no byte in time, this
 * document polls from then on, so the next stream does not wait again.
 *
 * The contract is EE's (`?transport=stream`: open with a comment at once;
 * `?transport=poll&wait=<ms>`: one window that ends as soon as it carried
 * frames, or empty at the deadline, at most 25 s; Last-Event-ID resumes it),
 * the same one the Console's conversation stream uses. Only a route that
 * implements it may be opened with `transport: "auto"`.
 */
export const STREAM_MODE = Object.freeze({ STREAM: "stream", POLL: "poll" });

/** No first byte within this long means a buffering proxy (CH-12's bar is 10 s). */
export const BUFFERING_TIMEOUT_MS = 8000;
/** The longest window EE holds open (WORKSPACE_POLL_MAX_WAIT_MS). */
export const POLL_MAX_WAIT_MS = 25000;
/** The shortest window EE accepts (`wait` below 1000 ms is a 400). */
export const POLL_MIN_WAIT_MS = 1000;
/** A window that has not ended this long after its own deadline is abandoned. */
export const POLL_GRACE_MS = 10000;

let documentMode = STREAM_MODE.STREAM;

export function currentStreamMode() {
  return documentMode;
}

/** Called once a stream proved buffered; every later "auto" stream starts in poll. */
export function settleOnPolling() {
  documentMode = STREAM_MODE.POLL;
}

/** Tests only: forget what this document learned. */
export function resetStreamMode() {
  documentMode = STREAM_MODE.STREAM;
}

/** The query a connection in `mode` adds to the caller's own. */
export function transportQuery(mode, pollWaitMs = POLL_MAX_WAIT_MS) {
  if (mode === STREAM_MODE.POLL) {
    const wait = Math.min(
      POLL_MAX_WAIT_MS,
      Math.max(POLL_MIN_WAIT_MS, Math.round(pollWaitMs)),
    );
    return { transport: STREAM_MODE.POLL, wait };
  }
  return { transport: STREAM_MODE.STREAM };
}
