import { RESPONSE_CODES } from "./constants";

const DEFAULT_RATE_LIMIT_MESSAGE = "Rate limit reached.";
const DEFAULT_RETRY_GUIDANCE = "Please try again in a few minutes.";

const hasTerminalPunctuation = (message) => /[.!?]$/.test(message);

const pickMessage = (...messages) =>
  messages.find(
    (message) => typeof message === "string" && message.trim().length > 0,
  ) || "";

function withRetryGuidance(message, retryAction) {
  const baseMessage = (message || DEFAULT_RATE_LIMIT_MESSAGE).trim();
  const guidance = retryAction
    ? `Please try ${retryAction} again in a few minutes.`
    : DEFAULT_RETRY_GUIDANCE;

  return `${baseMessage}${hasTerminalPunctuation(baseMessage) ? " " : ". "}${guidance}`;
}

export function getRequestErrorMessage(
  error,
  fallback = "Something went wrong",
  options = {},
) {
  const { retryAction } = options;
  const responseData = error?.response?.data || {};
  const statusCode =
    error?.response?.status ||
    responseData?.statusCode ||
    error?.status ||
    error?.statusCode;

  const extractedMessage = pickMessage(
    responseData?.result,
    responseData?.message,
    responseData?.error,
    responseData?.detail,
    error?.result,
    error?.message,
  );

  if (statusCode === RESPONSE_CODES.LIMIT_REACHED) {
    return withRetryGuidance(extractedMessage || fallback, retryAction);
  }

  return extractedMessage || fallback;
}
