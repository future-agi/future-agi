import { getSafeActionErrorMessage } from "src/utils/errorUtils";
import { retryAfterSeconds } from "src/utils/retryAfter";

export function samplingRequestError(error, requestName, fallback) {
  if ((error?.response?.status ?? error?.statusCode) !== 429) {
    const status = error?.response?.status ?? error?.statusCode;
    const transportCode = error?.transportCode ?? error?.code;
    if (
      [502, 503, 504].includes(status) ||
      (!status &&
        (error?.request ||
          [
            "ERR_NETWORK",
            "ECONNABORTED",
            "ETIMEDOUT",
            "ECONNREFUSED",
            "ECONNRESET",
          ].includes(transportCode) ||
          /network error|timeout|connection refused/i.test(
            error?.message || "",
          )))
    ) {
      return `Sampling ${requestName} is temporarily unavailable. Please try again in a moment.`;
    }
    // Axios normalizes the response body onto the error. Reuse the global
    // sanitizer for both shapes, including safe detail/message validation copy.
    const message = getSafeActionErrorMessage(
      error?.response ? error : { response: { status, data: error } },
      fallback,
    );
    const policyAdvice =
      /(?:check|change|edit|fix|correct|close|invalid|must|required|requires).*(?:policy|quota|window)|(?:policy|quota|window).*(?:invalid|must|required|requires|should)/i;
    return status !== 400 && status !== 422 && policyAdvice.test(message)
      ? fallback
      : message;
  }
  const seconds = retryAfterSeconds(error?.retryAfterSeconds);
  const wait =
    seconds === undefined
      ? "Please wait before trying again."
      : `Try again in ${seconds} seconds.`;
  return `Too many ${requestName} requests. ${wait}`;
}
