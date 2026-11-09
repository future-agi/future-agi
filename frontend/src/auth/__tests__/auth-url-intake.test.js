import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readAuthRedirectParams } from "../auth-url-intake";
import {
  flows,
  intake,
  onboardingStorageKey,
  parsedIndex,
  resetIntake,
  runIntake,
  stashKey,
  visit,
} from "./auth-url-fixtures";

beforeEach(() => {
  resetIntake();
  window.localStorage.clear();
  window.sessionStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
  resetIntake();
});

describe("auth redirect first-script intake", () => {
  it("runs first in head before GTM, config.js, and main.jsx", () => {
    const scripts = [...parsedIndex.querySelectorAll("script")];
    expect(scripts[0]).toBe(intake);
    expect(intake.parentElement).toBe(parsedIndex.head);
    for (const marker of ["googletagmanager", "config.js", "main.jsx"]) {
      expect(
        scripts.findIndex((script) => script.outerHTML.includes(marker)),
      ).toBeGreaterThan(0);
    }
    expect(
      [...parsedIndex.head.children].slice(0, 2).map((el) => el.tagName),
    ).toEqual(["META", "META"]);
  });

  it.each(flows)(
    "strips %s before a later script while retaining all unrelated URL bytes",
    (name, pathname, query) => {
      const suffix =
        "keep=a%20b&keep=c%2Fd&flag&returnTo=%2Fshared%2Ffake-path";
      visit(`${pathname}?${query}&${suffix}#tab=overview`);
      const state = window.history.state;
      runIntake();
      expect(window.location.pathname).toBe(pathname);
      expect(window.location.search).toBe(`?${suffix}`);
      expect(window.location.hash).toBe("#tab=overview");
      expect(window.history.state).toEqual(state);
      expect(Object.getOwnPropertyDescriptor(window, stashKey).enumerable).toBe(
        false,
      );
      expect(Object.keys(window)).not.toContain(stashKey);
      const held = window[stashKey];
      for (const [key, value] of new URLSearchParams(query)) {
        expect(held.params.get(key), `${name}: ${key}`).toBe(value);
      }
      expect(held.pathname).toBe(pathname);
      expect(held.params.has("keep")).toBe(false);
      expect(window[stashKey]).toBe(held); // Reads are not destructive.
      // Model a telemetry script that starts after the intake.
      // eslint-disable-next-line no-new-func
      const laterUrl = new Function("return window.location.href")();
      expect(laterUrl).toBe(
        `${window.location.origin}${pathname}?${suffix}#tab=overview`,
      );
      expect(window.localStorage.length).toBe(0);
      if (name === "AWS signup" || name === "GCP signup") {
        // Item 647 permits only the onboarding pair until consumption.
        expect(window.sessionStorage.length).toBe(1);
        expect(
          JSON.parse(window.sessionStorage.getItem(onboardingStorageKey)),
        ).toEqual({ pathname, params: [...new URLSearchParams(query)] });
        readAuthRedirectParams().clear();
      }
      expect(window.sessionStorage.length).toBe(0);
      expect(JSON.stringify(state)).not.toContain("fake-");
      expect(document.cookie).not.toContain("fake-");
    },
  );

  it.each([
    "sso_token",
    "access_token",
    "refresh_token",
    "onboarding_token",
    "onboarding_gcp_token",
  ])(
    "takes %s even when empty, duplicated or encoded on an arbitrary route",
    (key) => {
      const encodedKey = `%${key.charCodeAt(0).toString(16)}${key.slice(1)}`;
      visit(
        `/arbitrary?${encodedKey}=&${key}=fake-second&auth=saml&next=%2Fx&is_new_user=true&new_org=false&keep=1`,
      );
      runIntake();
      expect(window.location.search).toBe("?keep=1");
      expect(window[stashKey].params.get(key)).toBe("");
      expect(window[stashKey].params.getAll(key)).toEqual(["", "fake-second"]);
      expect([...window[stashKey].params.keys()]).toContain("new_org");
    },
  );

  it("cleans a same-origin double-slash pathname without resolving it as a host", () => {
    const pathname = "//fake-other-host.invalid/dashboard/develop";
    const suffix = "keep=a%20b&keep=c%2Fd&flag";
    const origin = window.location.origin;
    visit(
      `${origin}${pathname}?sso_token=fake-double-slash&is_new_user=true&${suffix}#stay`,
    );
    const state = window.history.state;
    runIntake();
    expect(window.location.origin).toBe(origin);
    expect(window.location.pathname).toBe(pathname);
    expect(window.location.search).toBe(`?${suffix}`);
    expect(window.location.hash).toBe("#stay");
    expect(window.history.state).toEqual(state);
    expect(window[stashKey].pathname).toBe(pathname);
    expect(window[stashKey].params.get("sso_token")).toBe("fake-double-slash");
    expect(window[stashKey].params.get("is_new_user")).toBe("true");
    // A script loaded later observes the same origin and the cleaned URL.
    // eslint-disable-next-line no-new-func
    expect(new Function("return window.location.href")()).toBe(
      `${origin}${pathname}?${suffix}#stay`,
    );
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it("does not expose an auth stash if native replaceState rejects cleanup", () => {
    visit("/dashboard?sso_token=fake-rejected&keep=1#stay");
    const before = window.location.href;
    vi.spyOn(window.history, "replaceState").mockImplementation(() => {
      throw new DOMException("Fake history rejection", "SecurityError");
    });
    expect(() => runIntake()).not.toThrow();
    expect(window[stashKey]).toBeUndefined();
    expect(window.location.href).toBe(before);
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it.each(["access", "refresh"])(
    "takes an incomplete SOS redirect with only %s",
    (key) => {
      visit(`/sos?${key}=fake-sos&next=%2Fx&keep=1#stay`);
      runIntake();
      expect(window.location.search).toBe("?keep=1");
      expect(window[stashKey].params.get(key)).toBe("fake-sos");
      expect(window.location.hash).toBe("#stay");
    },
  );

  it.each([
    "/auth/jwt/invitation/accept/fake-uuid/fake-token",
    "/auth/jwt/invitation/set-password/fake-uuid/fake-token",
    "/auth/jwt/verify/fake-uuid/fake-token",
    "/shared/fake-token",
    "/review/fake-token",
    "/dashboard/hub/verify-email?token=fake-hub",
    "/other?access=fake-not-sos&refresh=fake-not-sos",
    "/sos/child?access=fake-not-sos&refresh=fake-not-sos",
    "/auth/jwt/login?returnTo=%2Fshared%2Ffake-token&next=%2Freview%2Ffake-token",
    "/ordinary?is_new_user=true&new_org=true&auth=saml&next=%2Fdashboard",
  ])("does not change the non-intake URL %s", (url) => {
    visit(`${url}${url.includes("?") ? "&" : "?"}keep=1#fragment`);
    const before = window.location.href;
    runIntake();
    expect(window.location.href).toBe(before);
    expect(window[stashKey]).toBeUndefined();
  });

  it("keeps generic access and refresh outside SOS even alongside an auth credential", () => {
    visit(
      "/other?sso_token=fake-oauth&access=ordinary&refresh=ordinary&code=ordinary&token=ordinary",
    );
    runIntake();
    expect(window.location.search).toBe(
      "?access=ordinary&refresh=ordinary&code=ordinary&token=ordinary",
    );
    expect(window[stashKey].params.has("access")).toBe(false);
  });

  it("composes with the existing Omega connect fragment intake", () => {
    visit(
      "/dashboard/studio?sso_token=fake-oauth&keep=1#connect=http%3A%2F%2F127.0.0.1%3A4190&t=fake-connect&tab=runs",
    );
    runIntake();
    expect(window.location.search).toBe("?keep=1");
    expect(window.location.hash).toBe("#tab=runs");
    expect(window[stashKey].params.get("sso_token")).toBe("fake-oauth");
    expect(window.__omegaConnect.token).toBe("fake-connect");
  });

  it("does not replace the stash on a second script run and clears it at pagehide", () => {
    visit("/dashboard?sso_token=fake-oauth");
    runIntake();
    const held = window[stashKey];
    expect(held).toBeDefined();
    runIntake();
    expect(window[stashKey]).toBe(held);
    window.dispatchEvent(new Event("pagehide"));
    expect(window[stashKey]).toBeUndefined();
  });
});
