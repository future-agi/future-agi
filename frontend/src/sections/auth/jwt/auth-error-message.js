import { LOGIN_ERROR_CODES } from "src/utils/constants";

const DEFAULT_AUTH_ERROR_MESSAGE = "Something went wrong. Please try again.";

const indexedStringFromObject = (value) => {
  if (!value || typeof value !== "object") {
    return null;
  }

  const indexedKeys = Object.keys(value).filter(
    (key) => !Number.isNaN(Number(key)),
  );
  if (!indexedKeys.length) {
    return null;
  }

  return indexedKeys
    .sort((a, b) => Number(a) - Number(b))
    .map((key) => value[key])
    .join("");
};

const firstString = (...values) =>
  values.find((value) => typeof value === "string" && value.trim().length > 0);

export const getAuthErrorMessage = (
  error,
  fallback = DEFAULT_AUTH_ERROR_MESSAGE,
) => {
  if (typeof error === "string") {
    return error;
  }

  const indexedMessage = indexedStringFromObject(error);
  if (indexedMessage) {
    return indexedMessage;
  }

  const payload = error?.response?.data || error?.data || error || {};
  const result = payload?.result || {};
  const errorCode = result?.error_code || payload?.error_code;

  if (
    errorCode === LOGIN_ERROR_CODES.IP_BLOCKED ||
    errorCode === LOGIN_ERROR_CODES.IP_RATE_LIMITED
  ) {
    return (
      firstString(result.error, payload.error) ||
      "Your IP has been temporarily blocked. Please try again later."
    );
  }

  if (
    errorCode === LOGIN_ERROR_CODES.ACCOUNT_BLOCKED ||
    errorCode === LOGIN_ERROR_CODES.TOO_MANY_ATTEMPTS
  ) {
    const remaining =
      result.block_time_remaining || payload.block_time_remaining;
    const minutes = remaining ? Math.ceil(remaining / 60) : null;
    return minutes
      ? `Account temporarily blocked. Please try again in ${minutes} minutes.`
      : "Account temporarily blocked due to too many failed attempts.";
  }

  if (errorCode === LOGIN_ERROR_CODES.RECAPTCHA_FAILED) {
    return "reCAPTCHA verification failed. Please try again.";
  }

  if (errorCode === LOGIN_ERROR_CODES.INVALID_CREDENTIALS) {
    return "Enter a valid Email and password combination";
  }

  if (errorCode === LOGIN_ERROR_CODES.ACCOUNT_DEACTIVATED) {
    return (
      firstString(result.message, payload.message) ||
      "Your account has been deactivated. Please contact your organization admin."
    );
  }

  // Backward-compatible string matching for legacy responses without an
  // error_code. Keyed on specific server strings so it never mislabels an
  // unrelated error.
  const detail = firstString(payload.detail, error?.detail);
  if (detail === "User not found") {
    return "No account found with this email. Please sign up to create one";
  }
  if (result.error === "Invalid credentials") {
    return "Enter a valid Email and password combination";
  }
  if (result.error === "Account deactivated") {
    return (
      firstString(result.message) ||
      "Your account has been deactivated. Please contact your organization admin."
    );
  }

  return (
    firstString(
      result.message,
      result.error,
      result.detail,
      payload.message,
      payload.error,
      payload.detail,
      error?.message,
    ) || fallback
  );
};
