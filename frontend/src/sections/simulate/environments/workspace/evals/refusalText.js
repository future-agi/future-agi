// The one reader of a refused request's body, for every eval screen.
//
// The axios interceptor rejects with the API body itself, and that body can
// carry its sentence under any of `detail`, `message`, `error` or `result` —
// all optional, all nullable. Reading only one of them means the same refusal
// is quoted verbatim on one screen and silently replaced by a generic sentence
// on the next, so every call site reads them in the same order here.
//
// `result` is last and is only used when it is a string: the platform's global
// error handler keys on `result`, and it is just as often an object (field
// errors, an error code) — rendering that would put "[object Object]" on
// screen.
//
// `message` is only trusted when the body came back from the API at all. A
// request that never reached the server, and one the client refuses to send,
// both reject with a `message` written by our own plumbing — a transport
// string, or an internal sentence about the request itself. Neither is
// something to show a user in place of the caller's written fallback, and a
// status code is what separates them from a refusal the server worded.
const firstSentence = (error) => {
  const fromApi = error?.statusCode != null;
  const candidates = [error?.detail, fromApi ? error?.message : undefined, error?.error];
  const found = candidates.find((v) => typeof v === "string" && v.trim());
  if (found) return found;
  return typeof error?.result === "string" && error.result.trim() ? error.result : null;
};

/**
 * The words to show for a refused request.
 * @param {unknown} error     The rejected value from the axios interceptor.
 * @param {string} fallback   Shown when the body carries no usable sentence.
 * @returns {string}
 */
export function refusalText(error, fallback) {
  return firstSentence(error) || fallback;
}

export default refusalText;
