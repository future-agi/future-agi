import { trackPostHogEvent } from "src/utils/PostHog";

export const SignupEntryEvents = {
  signupSubmitted: "signup_email_submitted",
  signupSucceeded: "signup_email_succeeded",
  signupFailed: "signup_email_failed",
  loginSubmitted: "signup_login_submitted",
  loginSucceeded: "signup_login_succeeded",
  loginFailed: "signup_login_failed",
};

const UNSAFE_ERROR_TEXT_KEYS = new Set(["password", "token"]);
const EMAIL_ADDRESS_PATTERN = /[^\s@]+@[^\s@]+\.[^\s@]+/;

const compactProperties = (properties = {}) =>
  Object.entries(properties).reduce((result, [key, value]) => {
    if (value === undefined || value === null || value === "") {
      return result;
    }
    result[key] = value;
    return result;
  }, {});

const safeErrorCode = (error) => {
  const candidate =
    error?.result?.code ||
    error?.response?.data?.result?.code ||
    error?.response?.data?.code ||
    error?.code ||
    error?.status;

  if (!candidate) return undefined;
  return String(candidate)
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_:-]+/g, "_")
    .slice(0, 80);
};

const safeErrorMessage = (error) => {
  const candidate =
    error?.result?.message ||
    error?.response?.data?.result?.message ||
    error?.response?.data?.message ||
    error?.message;

  if (!candidate || typeof candidate !== "string") return undefined;
  const normalized = candidate.trim();
  if (!normalized) return undefined;
  const lower = normalized.toLowerCase();
  if (
    EMAIL_ADDRESS_PATTERN.test(normalized) ||
    [...UNSAFE_ERROR_TEXT_KEYS].some((key) => lower.includes(key))
  ) {
    return undefined;
  }
  return normalized.slice(0, 140);
};

export const buildSignupEntryProperties = ({
  authFlow,
  error,
  hasPassword,
  method = "email",
  onboardingToken,
  returnTo,
  status,
} = {}) =>
  compactProperties({
    method,
    auth_flow: authFlow,
    status,
    has_password: Boolean(hasPassword),
    onboarding_token_present: Boolean(onboardingToken),
    return_to_present: Boolean(returnTo),
    error_code: safeErrorCode(error),
    error_message: safeErrorMessage(error),
  });

export const trackSignupEntryEvent = (eventName, properties = {}) => {
  if (!eventName) return false;
  trackPostHogEvent(eventName, buildSignupEntryProperties(properties));
  return true;
};
