import { DatasetLimitCheckFailedErrorApiCode } from "src/generated/api-contracts/api.schemas";
import { isWorkspaceRecoveryHandled } from "src/contexts/workspaceRefusalHold";
import { RESPONSE_CODES } from "./constants";

// This is the only gateway authentication text trusted for display. A backend
// proxy must never be allowed to surface arbitrary upstream credential details.
const GATEWAY_AUTHENTICATION_REMEDY =
  "Gateway authentication failed. Ask your administrator to check the gateway credentials.";

// Null means the caller must keep its existing copy, including its precedence.
// Do not flatten or mutate the AxiosError: session handling belongs to axios.js.
export function getGatewayAuthenticationErrorMessage(error) {
  if (
    error?.response?.status !== 401 ||
    error?.response?.data?.code !== "gateway_request_rejected"
  )
    return null;

  const message = error.response.data.message;
  return message === GATEWAY_AUTHENTICATION_REMEDY
    ? message
    : GATEWAY_AUTHENTICATION_REMEDY;
}

const DEFAULT_RATE_LIMIT_MESSAGE = "Rate limit reached.";
const DEFAULT_RETRY_GUIDANCE = "Please try again in a few minutes.";

const hasTerminalPunctuation = (message) => /[.!?]$/.test(message);

const pickMessage = (...messages) =>
  messages.find(
    (message) => typeof message === "string" && message.trim().length > 0,
  ) || "";

const SAFE_VALIDATION_STATUS_CODES = new Set([400, 404, 409, 422]);
// Typed API error codes whose message the backend writes for users
// (futureagi/tfc/utils/error_codes.py), shown whatever their status.
// `permission_denied` is DRF's PermissionDenied code: its message says what
// the user may not do (a dataset access denial also names who can grant it).
const USER_FACING_ERROR_CODES = new Set([
  ...Object.values(DatasetLimitCheckFailedErrorApiCode),
  "permission_denied",
]);
const INTERNAL_ERROR_MARKERS =
  /DB::|ClickHouse|Stack\s*trace|Traceback|Code:\s*\d+|SELECT\s|maximum:\s*\d+|elapsed\s+\d+/i;

/**
 * Return concise validation feedback, but never expose infrastructure/query
 * details from a failed mutation. Server-side failures without a user-facing
 * code, and suspiciously large or multiline payloads, intentionally collapse
 * to the caller's safe fallback.
 */
export function getSafeActionErrorMessage(error, fallback) {
  const gatewayRemedy = getGatewayAuthenticationErrorMessage(error);
  if (gatewayRemedy) return gatewayRemedy;
  // A refusal the workspace recovery explains keeps its exact server text,
  // which the snackbar gate drops; a fallback would show as a red line.
  const recoveryHandled = isWorkspaceRecoveryHandled(error);
  const responseData = error?.response?.data || {};
  const statusCode = Number(
    error?.response?.status ||
      responseData?.statusCode ||
      error?.status ||
      error?.statusCode,
  );
  const code = responseData?.code || error?.code;
  const message = pickMessage(
    responseData?.message,
    responseData?.detail,
    responseData?.error,
    responseData?.result,
    error?.result,
    ...(recoveryHandled ? [error.message, error.detail, error.error] : []),
  ).trim();

  // The refusal's fixed copy ("Select an accessible workspace...") trips the
  // SELECT marker below, so a handled refusal skips only that heuristic.
  if (
    recoveryHandled &&
    message &&
    message.length <= 240 &&
    !/[\r\n]/.test(message)
  ) {
    return message;
  }
  if (
    !(
      SAFE_VALIDATION_STATUS_CODES.has(statusCode) ||
      USER_FACING_ERROR_CODES.has(code)
    ) ||
    !message ||
    message.length > 240 ||
    /[\r\n]/.test(message) ||
    INTERNAL_ERROR_MARKERS.test(message)
  ) {
    return fallback;
  }
  return message;
}

const WORKSPACE_CHANGED_ACTION_LINE = "Your workspace changed. Try again.";

// Inline error UI (an Alert, or an action-error line a screen renders) does
// not pass through the snackbar gate. For a refusal the workspace recovery
// handles it shows one calm line, never the server's refusal text: the
// recovery may add its own line naming where the person landed, or end
// without one (a workspace was chosen during its read), and a screen that
// reports errors only inline must not go silent either way.
export function getInlineActionErrorMessage(error, fallback) {
  if (isWorkspaceRecoveryHandled(error)) return WORKSPACE_CHANGED_ACTION_LINE;
  return getSafeActionErrorMessage(error, fallback);
}

const WORKSPACE_CHANGED_LOAD_LINE =
  "Your workspace changed before this loaded.";

// A failed read whose error panel is the only content (often with Retry):
// for a handled refusal it says calmly what happened instead of going blank.
export function getInlineLoadErrorMessage(error, fallback) {
  if (isWorkspaceRecoveryHandled(error)) return WORKSPACE_CHANGED_LOAD_LINE;
  return getSafeActionErrorMessage(error, fallback);
}

// CBSEC S2/S3/S4: fixed copy approved for the run-test execution action only.
// Do not add 5xx statuses or generic server error codes to the safe action filter.
export const TEST_EXECUTION_ERROR_MESSAGES = Object.freeze([
  "Failed to start test execution. Check the test runs before trying again.",
  "Test execution service is unavailable. Please try again later.",
  "Failed to execute test. Check the test runs before trying again.",
]);

export function getTestExecutionErrorMessage(error) {
  const fallback = "Something went wrong";
  const responseData = error?.response?.data || {};
  const statusCode = Number(
    error?.response?.status ||
      responseData?.statusCode ||
      error?.status ||
      error?.statusCode,
  );
  if (statusCode >= 500 && statusCode < 600) {
    // Compare raw strings: trimming, joining or prefix matching would widen
    // the approved bytes. Error codes must not bypass this exact allowlist.
    const message = pickMessage(
      responseData?.message,
      responseData?.detail,
      responseData?.error,
      responseData?.result,
      error?.message,
      error?.detail,
      error?.error,
      error?.result,
    );
    return TEST_EXECUTION_ERROR_MESSAGES.includes(message) ? message : fallback;
  }
  if (statusCode >= 400 && statusCode < 500) {
    return getSafeActionErrorMessage(error, fallback);
  }
  return fallback;
}

function withRetryGuidance(message, retryAction) {
  const baseMessage = (message || DEFAULT_RATE_LIMIT_MESSAGE).trim();
  const guidance = retryAction
    ? `Please try ${retryAction} again in a few minutes.`
    : DEFAULT_RETRY_GUIDANCE;

  return `${baseMessage}${hasTerminalPunctuation(baseMessage) ? " " : ". "}${guidance}`;
}

// Keys are `UserSignupSerializer.Meta.fields`, values the inputs the signup form
// renders. Deliberately not a generic snake-to-camel transform: a backend field
// with no input on screen has nowhere to show, so it falls through to the
// form-level message instead.
const SIGNUP_FIELD_TO_FORM_FIELD = {
  email: "email",
  password: "password",
  full_name: "fullName",
};

export function getSignupFieldErrors(error) {
  if (error?.result?.error_code !== "SIGNUP_VALIDATION_FAILED") return null;

  const fieldErrors = error?.result?.field_errors;
  if (!fieldErrors || typeof fieldErrors !== "object") return null;

  const fields = [];
  const messages = [];

  Object.entries(fieldErrors).forEach(([key, value]) => {
    const message = [].concat(value).join(" ");
    const name = SIGNUP_FIELD_TO_FORM_FIELD[key];
    if (name) {
      fields.push({ name, message });
    } else if (key === "non_field_errors") {
      // DRF's bucket for errors belonging to no single field: show it unlabelled.
      messages.push(message);
    } else {
      messages.push(`${key}: ${message}`);
    }
  });

  return { fields, message: messages.join(" ") };
}

export function getRequestErrorMessage(
  error,
  fallback = "Something went wrong",
  options = {},
) {
  const gatewayRemedy = getGatewayAuthenticationErrorMessage(error);
  if (gatewayRemedy) return gatewayRemedy;
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
