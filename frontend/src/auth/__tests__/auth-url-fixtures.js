/* eslint-env node */
import fs from "node:fs";
import path from "node:path";

export const indexHtml = fs.readFileSync(
  path.resolve(__dirname, "../../../index.html"),
  "utf8",
);
export const parsedIndex = new DOMParser().parseFromString(
  indexHtml,
  "text/html",
);
export const intake = parsedIndex.querySelector(
  "script[data-omega-connect-intake]",
);
export const stashKey = "__faiAuthRedirect";
export const onboardingStorageKey = "fai.auth.onboarding.v1";
export const visit = (url) =>
  window.history.replaceState({ marker: "kept" }, "", url);

export function runIntake() {
  // Execute only the real, first inline script, never the analytics scripts.
  // eslint-disable-next-line no-new-func
  new Function(intake.textContent)();
}

export function resetIntake() {
  window.sessionStorage.clear();
  delete window[stashKey];
  delete window.__omegaConnect;
  visit("/");
}

export const flows = [
  [
    "Google",
    "/dashboard/develop",
    "sso_token=fake-google&is_new_user=true&next=%2Fdashboard%2Fruns",
  ],
  ["GitHub", "/dashboard/develop", "sso_token=fake-github&is_new_user=false"],
  [
    "Microsoft",
    "/dashboard/develop",
    "sso_token=fake-microsoft&is_new_user=true",
  ],
  [
    "SAML",
    "/dashboard/develop",
    "sso_token=fake-saml&next=%2Fdashboard%2Fruns&auth=saml",
  ],
  [
    "AWS launch",
    "/dashboard/develop",
    "access_token=fake-aws&refresh_token=fake-aws-refresh&new_org=true",
  ],
  ["AWS signup", "/auth/jwt/register", "onboarding_token=fake-aws-onboarding"],
  [
    "GCP signup",
    "/auth/jwt/register",
    "onboarding_gcp_token=fake-gcp-onboarding",
  ],
  ["SOS", "/sos", "access=fake-sos&refresh=fake-sos-refresh"],
];
