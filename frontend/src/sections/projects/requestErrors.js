import { getSafeActionErrorMessage } from "src/utils/errorUtils";

// Why an action failed, in words that don't blame the person for a fault on
// the server's side. A 5xx, or a request that never got an answer, is no one's
// input mistake: say it couldn't be done right now and that trying again may
// work. A request the server refused (4xx) shows the server's own message
// when it is safe to, else the caller's words.

export const RETRY_LATER = "Couldn't do that right now. Try again in a moment.";

/**
 * A failed request's status, as the app's axios rejects it (the response's
 * error body with `statusCode`) or a raw Axios error carries it; null when
 * there was no answer at all.
 */
export function errorStatus(error) {
  const status = Number(error?.response?.status ?? error?.statusCode);
  return Number.isFinite(status) && status > 0 ? status : null;
}

/** The server failed (5xx), or nothing answered (a network error). */
export const isServerFault = (error) => {
  const status = errorStatus(error);
  return status == null || status >= 500;
};

/**
 * What to say when an action fails: `retry` for a fault on the server's side;
 * for a refusal, the server's message when safe to show, else `fallback`.
 */
export const actionErrorMessage = (error, { fallback, retry = RETRY_LATER }) =>
  isServerFault(error) ? retry : getSafeActionErrorMessage(error, fallback);
