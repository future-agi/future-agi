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
