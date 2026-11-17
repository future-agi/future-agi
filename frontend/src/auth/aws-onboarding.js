import axios from "axios";
import { apiPath } from "src/api/contracts/api-surface";
import { HOST_API } from "src/config-global";

const STORAGE_KEY = "fai.auth.aws-onboarding.v1";
const attempts = new Map();

// The redirect code remains the intake identity. Only this body-only form
// capability is submitted, and it never goes into a URL or localStorage.
export function clearAWSOnboardingAttempt(code) {
  if (code) attempts.delete(code);
  else attempts.clear();
  try {
    const stored = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY));
    if (!code || stored?.code === code) {
      window.sessionStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // Blocked storage must not prevent in-memory cleanup.
  }
}

export function prepareAWSOnboarding(code) {
  if (attempts.has(code)) return attempts.get(code);
  try {
    const stored = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY));
    if (
      stored?.code === code &&
      typeof stored.attempt === "string" &&
      stored.attempt
    ) {
      return Promise.resolve(stored.attempt);
    }
  } catch {
    // StrictMode/remounts still share the in-memory request without storage.
  }

  // Like a login handoff, this is anonymous and must not run the session
  // refresh interceptor (or carry a stale global Authorization default).
  const request = axios
    .post(
      apiPath("/accounts/aws-marketplace/onboarding-exchange/"),
      { code },
      {
        baseURL: HOST_API,
        withCredentials: false,
        headers: { Authorization: null },
      },
    )
    .then(({ data }) => {
      const attempt = data?.onboarding_attempt;
      if (typeof attempt !== "string" || !attempt) {
        throw new Error("Onboarding exchange failed");
      }
      // A late response cannot resurrect recovery after logout or success.
      if (attempts.get(code) === request) {
        try {
          window.sessionStorage.setItem(
            STORAGE_KEY,
            JSON.stringify({ code, attempt }),
          );
        } catch {
          // A mounted form can continue even when storage is unavailable.
        }
      }
      return attempt;
    })
    .catch(() => {
      if (attempts.get(code) === request) attempts.delete(code);
      // Never retain an Axios error containing the submitted code.
      throw new Error("Onboarding exchange failed");
    });
  attempts.set(code, request);
  return request;
}
