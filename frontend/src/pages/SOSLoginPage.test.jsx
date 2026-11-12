import React, { StrictMode } from "react";
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  resetIntake,
  runIntake,
  stashKey,
  visit,
} from "src/auth/__tests__/auth-url-fixtures";
import SOSLoginPage from "./SOSLoginPage";

// CR-07d: the SOS link carries only a
// one-time code. The page redeems it by POST, takes both tokens from the
// response body, and never stores access/refresh values found in the URL.

const REDEEM_URL = "https://api.handoff.test/accounts/handoff/redeem/";
const REFUSED_TEXT = "Link expired, sign in again.";
// synthetic test value
const SYNTHETIC_CODE = "synthetic-sos-code-not-a-credential";
// synthetic test value
const SYNTHETIC_ACCESS = "synthetic-sos-access-not-a-credential";
// synthetic test value
const SYNTHETIC_REFRESH = "synthetic-sos-refresh-not-a-credential";
// synthetic test value
const SYNTHETIC_LEGACY = "synthetic-legacy-url-token-not-a-credential";

const LEGACY_QUERY = [
  "access",
  "refresh",
  "sso_token",
  "access_token",
  "refresh_token",
]
  .map((key) => `${key}=${SYNTHETIC_LEGACY}`)
  .join("&");

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  post: vi.fn(),
  logout: vi.fn(),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ logout: mocks.logout }),
}));
vi.mock("axios", () => ({ default: { post: mocks.post } }));
vi.mock("src/config-global", () => ({ HOST_API: "https://api.handoff.test" }));
vi.mock("react-router-dom", () => ({ useNavigate: () => mocks.navigate }));

function captureDocumentNavigation() {
  const browser = window;
  const destinations = [];
  const locationView = {
    get search() { return browser.location.search; },
    get pathname() { return browser.location.pathname; },
    get href() { return browser.location.href; },
    set href(value) { destinations.push(value); },
    replace(value) { destinations.push(value); },
  };
  vi.stubGlobal("window", new Proxy(browser, {
    get(target, key) { return key === "location" ? locationView : Reflect.get(target, key, target); },
  }));
  return destinations;
}

async function flush() {
  for (let turn = 0; turn < 5; turn += 1) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => {
      await Promise.resolve();
    });
  }
}

function renderPage() {
  return render(
    <StrictMode>
      <SOSLoginPage />
    </StrictMode>,
  );
}

function expectNoSession() {
  expect(localStorage.getItem("accessToken")).toBeNull();
  expect(localStorage.getItem("refreshToken")).toBeNull();
  expect(localStorage.getItem("sosMode")).toBeNull();
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.post.mockReset();
  mocks.logout.mockReset();
  mocks.logout.mockResolvedValue(undefined);
  resetIntake();
  vi.stubGlobal("localStorage", window.localStorage);
  vi.stubGlobal("sessionStorage", window.sessionStorage);
  localStorage.clear();
  sessionStorage.clear();
  vi.stubGlobal("crypto", { randomUUID: () => "FAKE-generation" });
  vi.useFakeTimers();
});
afterEach(async () => {
  cleanup();
  await act(async () => {});
  vi.useRealTimers();
  vi.unstubAllGlobals();
  resetIntake();
});

it("redeems once, commits both tokens at document exit and reloads the dashboard", async () => {
  visit(`/sos?code=${SYNTHETIC_CODE}`);
  mocks.post.mockResolvedValue({
    data: { access: SYNTHETIC_ACCESS, refresh: SYNTHETIC_REFRESH },
  });

  const destinations = captureDocumentNavigation();
  const view = renderPage();

  // The code leaves the address bar before the redeem settles.
  expect(window.location.search).toBe("");
  expectNoSession();
  await flush();
  expect(mocks.post).toHaveBeenCalledTimes(1);
  expect(mocks.post).toHaveBeenCalledWith(
    REDEEM_URL,
    { code: SYNTHETIC_CODE, kind: "sos" },
    expect.objectContaining({
      headers: expect.objectContaining({ "Content-Type": "application/json" }),
    }),
  );
  expectNoSession();
  expect(destinations).toEqual(["/dashboard/develop"]);
  act(() => window.dispatchEvent(new Event("pagehide")));
  expect(localStorage.getItem("sosMode")).toBe("true");
  expect(localStorage.getItem("accessToken")).toBe(SYNTHETIC_ACCESS);
  expect(localStorage.getItem("refreshToken")).toBe(SYNTHETIC_REFRESH);
  expect(view.container.textContent).toContain(
    "Page will refresh automatically...",
  );
  view.rerender(
    <StrictMode>
      <SOSLoginPage />
    </StrictMode>,
  );
  expect(mocks.navigate).not.toHaveBeenCalled();
  act(() => vi.advanceTimersByTime(1000));
  expect(mocks.navigate).not.toHaveBeenCalled();
  expect(destinations).toEqual(["/dashboard/develop"]);
  expect(mocks.post).toHaveBeenCalledTimes(1);
});

it("takes the tokens from the body, never from legacy params beside the code", async () => {
  visit(`/sos?code=${SYNTHETIC_CODE}&${LEGACY_QUERY}`);
  mocks.post.mockResolvedValue({
    data: { access: SYNTHETIC_ACCESS, refresh: SYNTHETIC_REFRESH },
  });

  captureDocumentNavigation();
  renderPage();
  await flush();
  act(() => window.dispatchEvent(new Event("pagehide")));

  expect(window.location.search).toBe("");
  expect(localStorage.getItem("accessToken")).toBe(SYNTHETIC_ACCESS);
  expect(localStorage.getItem("refreshToken")).toBe(SYNTHETIC_REFRESH);
});

describe.each(["URL fallback", "stash"])(
  "a legacy SOS link using %s",
  (source) => {
    it("is never stored as a session and leaves the address bar", async () => {
      visit(`/sos?${LEGACY_QUERY}&keep=1`);
      if (source === "stash") {
        runIntake();
        expect(window[stashKey]).toBeDefined();
      }

      const view = renderPage();
      await flush();
      act(() => vi.advanceTimersByTime(5000));

      expect(mocks.post).not.toHaveBeenCalled();
      expectNoSession();
      expect(window.location.search).toBe("?keep=1");
      expect(window[stashKey]).toBeUndefined();
      expect(view.container.textContent).toContain(REFUSED_TEXT);
      expect(view.container.textContent).not.toContain(SYNTHETIC_LEGACY);
      expect(mocks.navigate).not.toHaveBeenCalled();
    });
  },
);

it.each(["replayed", "expired", "unknown"])(
  "offers actionable recovery for a %s code without starting SOS mode",
  async () => {
    visit(`/sos?code=${SYNTHETIC_CODE}`);
    mocks.post.mockRejectedValue(
      Object.assign(new Error("refused"), {
        response: { status: 400, data: { error: "handoff_refused" } },
      }),
    );

    const view = renderPage();
    await flush();
    act(() => vi.advanceTimersByTime(5000));

    expect(mocks.post).toHaveBeenCalledTimes(1);
    expectNoSession();
    expect(view.getByRole("alert").textContent).toContain(REFUSED_TEXT);
    expect(view.container.textContent).toContain("Ask your support operator for a fresh SOS link.");
    expect(view.container.textContent).not.toContain("Setting up emergency access");
    expect(mocks.navigate).not.toHaveBeenCalled();
    fireEvent.click(view.getByRole("button", { name: "Sign in again" }));
    await flush();
    expect(mocks.logout).toHaveBeenCalledTimes(1);
    expect(mocks.navigate).toHaveBeenCalledWith("/auth/jwt/login", { replace: true });
  },
);

it("does not install an SOS session after unmount", async () => {
  visit(`/sos?code=${SYNTHETIC_CODE}`);
  let resolve;
  mocks.post.mockReturnValue(new Promise((done) => { resolve = done; }));
  const view = renderPage();
  view.unmount();
  resolve({ data: { access: SYNTHETIC_ACCESS, refresh: SYNTHETIC_REFRESH } });
  await flush();
  act(() => vi.advanceTimersByTime(5000));
  expectNoSession();
  expect(mocks.navigate).not.toHaveBeenCalled();
});

it.each(["browser", "copy-link"])("%s SOS reload commits after an old orgless initialization finishes", async () => {
  visit(`/sos?code=${SYNTHETIC_CODE}`);
  localStorage.setItem("accessToken", "FAKE-old-access");
  sessionStorage.setItem("organizationId", "FAKE-old-org");
  let finishRedeem;
  mocks.post.mockReturnValue(new Promise((resolve) => { finishRedeem = resolve; }));
  const destinations = captureDocumentNavigation();
  let finishOldInitialization;
  const oldInitialization = new Promise((resolve) => { finishOldInitialization = resolve; }).then(() => {
    // Model the existing provider's unguarded setSession(A) after its await.
    localStorage.setItem("accessToken", "FAKE-old-access");
    sessionStorage.setItem("organizationId", "FAKE-old-org");
    return { id: "FAKE-old-user", requires_org_setup: true };
  });
  renderPage();
  finishRedeem({ data: { access: SYNTHETIC_ACCESS, refresh: SYNTHETIC_REFRESH } });
  await flush();
  finishOldInitialization();
  await act(async () => { await oldInitialization; });
  expect(destinations).toEqual(["/dashboard/develop"]);
  expect(mocks.navigate).not.toHaveBeenCalled();
  // Full document navigation, not SPA routing, retires the old provider.
  act(() => window.dispatchEvent(new Event("pagehide")));
  expect(localStorage.getItem("accessToken")).toBe(SYNTHETIC_ACCESS);
  expect(localStorage.getItem("refreshToken")).toBe(SYNTHETIC_REFRESH);
  expect(localStorage.getItem("sosMode")).toBe("true");
  expect(sessionStorage.getItem("organizationId")).toBeNull();
});

it("treats a body without both tokens as refused", async () => {
  visit(`/sos?code=${SYNTHETIC_CODE}`);
  mocks.post.mockResolvedValue({ data: { access: SYNTHETIC_ACCESS } });

  const view = renderPage();
  await flush();
  act(() => vi.advanceTimersByTime(5000));

  expectNoSession();
  expect(view.container.textContent).toContain(REFUSED_TEXT);
  expect(mocks.navigate).not.toHaveBeenCalled();
});
