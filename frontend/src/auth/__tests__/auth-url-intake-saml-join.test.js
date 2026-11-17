import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  parsedIndex,
  resetIntake,
  runIntake,
  stashKey,
  visit,
} from "./auth-url-fixtures";

// The page's first script (the auth and connect intake) and the SAML guard
// after it both read sign-in callback URLs. A callback marked with saml_code
// belongs to the SAML guard: the intake hands nothing off from it, and the
// guard then removes every credential that came with the marker. Callbacks
// without saml_code keep the intake's handoff.
const initialGuard = parsedIndex.querySelector("#saml-initial-marker-guard");

function runInitialGuard() {
  // eslint-disable-next-line no-new-func
  new Function(initialGuard.textContent)();
}

// eslint-disable-next-line no-new-func
const address = () => new Function("return window.location.href")();

beforeEach(() => {
  resetIntake();
});
afterEach(() => {
  delete window.__takeSamlUrlSanitizer;
  delete window.__authHandoffScrubFailed;
  delete window.__authLateMarkerScrubFailed;
  resetIntake();
});

describe("the auth intake and the SAML guard on one page", () => {
  it("hands nothing off from a saml_code callback, and the guard leaves a clean address", () => {
    visit("/auth/callback?saml_code=fake-code&sso_token=fake-sso&keep=1");
    runIntake();
    expect(window[stashKey]).toBeUndefined();
    runInitialGuard();
    expect(address()).toBe(`${window.location.origin}/auth/callback?keep=1`);
  });

  it("does the same when saml_code is in the fragment", () => {
    visit("/auth/callback?sso_token=fake-sso#saml_code=fake-code");
    runIntake();
    expect(window[stashKey]).toBeUndefined();
    runInitialGuard();
    expect(address()).toBe(`${window.location.origin}/auth/callback`);
  });

  it("still hands off an unmarked sso_token, and the guard leaves it alone", () => {
    visit("/dashboard/develop?sso_token=fake-sso&keep=1");
    runIntake();
    expect(window[stashKey].params.get("sso_token")).toBe("fake-sso");
    runInitialGuard();
    expect(address()).toBe(
      `${window.location.origin}/dashboard/develop?keep=1`,
    );
  });
});
