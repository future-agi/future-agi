import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cwd } from "node:process";
import { runInNewContext } from "node:vm";
import { JSDOM } from "jsdom";
import { createElement } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// The page every self-hosted user loads first, before any React code runs.
const indexHtmlSource = readFileSync(resolve(cwd(), "index.html"), "utf8");

// Vite's %ENV% substitution in index.html: a key the build defines is
// replaced, any other is left as is.
const build = (env) =>
  indexHtmlSource.replace(/%(\S+?)%/g, (text, key) =>
    key in env ? env[key] : text,
  );

const GOOGLE_HOST = /google\.com|gstatic\.com|googletagmanager\.com/;

const urlsIn = (document) =>
  [...document.querySelectorAll("[src], [href]")].map(
    (el) => el.getAttribute("src") || el.getAttribute("href"),
  );

// Every URL the page asks for once its inline scripts ran, plus what a
// browser without JavaScript fetches: parsed with scripting off, <noscript>
// content is markup.
function googleUrls(env, url) {
  const html = build(env);
  const withScripts = new JSDOM(html, {
    runScripts: "dangerously",
    ...(url && { url }),
  });
  const withoutScripts = new JSDOM(html);
  const urls = new Set([
    ...urlsIn(withScripts.window.document),
    ...urlsIn(withoutScripts.window.document),
  ]);
  return [...urls].filter((url) => GOOGLE_HOST.test(url));
}

const origin = "https://app.example.test";
const taggedBuild = { VITE_GTM_ID: "GTM-TEST" };
const consent = "/mcp/authorize?request_id=example";
const loginReturningTo = (path) =>
  `/auth/jwt/login?${new URLSearchParams({ returnTo: path })}`;
const googleUrlsIn = (document) =>
  urlsIn(document).filter((url) => GOOGLE_HOST.test(url));

// Run the shipped head scripts in source order, without fetching resources.
// JSDOM implements same-document history, not document navigation. A fixture
// can also supply the missing native storage-serialization step before capture.
function runHead(path, beforeTag, env = taggedBuild, beforeHead) {
  const dom = new JSDOM(build(env), { url: new URL(path, origin).href });
  const { document, history } = dom.window;
  beforeHead?.(dom.window);
  const location = {
    get href() {
      return dom.window.location.href;
    },
    assign: vi.fn(),
    replace: vi.fn(),
    reload: vi.fn(),
  };
  const window = {
    history,
    location,
    addEventListener: dom.window.addEventListener.bind(dom.window),
  };
  const context = { window, document, URL, URLSearchParams };
  for (const script of document.head.querySelectorAll("script:not([src])")) {
    if (script.id === "google-tag-manager") beforeTag?.(window, dom);
    runInNewContext(script.textContent, context);
  }
  return { window, document, dom };
}

// JSDOM retains the input reference instead of serializing History state. Use
// a real clone for fixture data at the native boundary, not in the shipped guard.
function serializeFixtureState({ history }) {
  for (const method of ["pushState", "replaceState"]) {
    const native = history[method];
    history[method] = function (state, ...rest) {
      return Reflect.apply(native, this, [structuredClone(state), ...rest]);
    };
  }
}

describe("index.html third-party requests", () => {
  it("contacts no Google host when the build sets no key", () => {
    expect(googleUrls({})).toEqual([]);
  });

  it("contacts no Google host when the keys are set but empty", () => {
    expect(googleUrls({ VITE_GTM_ID: "", VITE_GOOGLE_SITE_KEY: "" })).toEqual(
      [],
    );
  });

  it("loads Tag Manager when the build sets VITE_GTM_ID", () => {
    expect(googleUrls({ VITE_GTM_ID: "GTM-TEST" })).toEqual([
      "https://www.googletagmanager.com/gtm.js?id=GTM-TEST",
    ]);
  });

  it.each([
    "https://app.example.test/auth/jwt/verify/uuid-1/token-1",
    "https://app.example.test/AUTH/JWT/VERIFY/uuid-1/token-1",
    "https://app.example.test/auth/jwt/invitation/accept/uuid-1/token-1",
    "https://app.example.test/auth/jwt/invitation/set-password/uuid-1/token-1",
    "https://app.example.test/shared/token-1",
    "https://app.example.test/sos",
    "https://app.example.test/sos/",
    "https://app.example.test/dashboard?sso_token=a&refresh_token=b",
    "https://app.example.test/dashboard?access_token=a",
    "https://app.example.test/auth/jwt/login#access_token=a",
    "https://app.example.test/mcp/consent?code=a",
    "https://app.example.test/%73hared/token-1",
    "https://app.example.test//shared/token-1",
    "https://app.example.test/auth/jwt/%76erify/uuid-1/token-1",
  ])("does not load Tag Manager on %s", (url) => {
    expect(googleUrls({ VITE_GTM_ID: "GTM-TEST" }, url)).toEqual([]);
  });

  it.each([
    "https://app.example.test/auth/jwt/login",
    "https://app.example.test/auth/jwt/verify",
    "https://app.example.test/dashboard/develop?utm_source=x",
    "https://app.example.test/sosx",
  ])("loads Tag Manager on %s", (url) => {
    expect(googleUrls({ VITE_GTM_ID: "GTM-TEST" }, url)).toEqual([
      "https://www.googletagmanager.com/gtm.js?id=GTM-TEST",
    ]);
  });

  it.each([
    ["registration", "/auth/jwt/register?onboarding_token=example"],
    [
      "alternate registration",
      "/auth/jwt/register?onboarding_gcp_token=example",
    ],
    ["forwarded sign-in", "/auth/jwt/login?onboarding_token="],
    ["fragment parameter", "/auth/jwt/register#onboarding_gcp_token=example"],
    [
      "registration return",
      "/auth/jwt/login?returnTo=%2Fauth%2Fjwt%2Fregister%3Fonboarding_token%3Dexample",
    ],
    ["authorization consent", consent],
    ["legacy authorization consent", "/mcp/authorize?client_id=example"],
    ["consent return", loginReturningTo(consent)],
    ["encoded consent return", loginReturningTo(encodeURIComponent(consent))],
  ])("leaves tags off for %s", (_name, path) => {
    const { window, document } = runHead(path);
    expect(window.__analyticsAllowedAtLoad).toBe(false);
    expect(window.__analyticsUrlAllowed(window.location.href)).toBe(false);
    expect(window.dataLayer).toBeUndefined();
    expect(googleUrlsIn(document)).toEqual([]);
  });

  it.each([
    [consent, true],
    ["/mcp/oauth/authorize", true],
    [loginReturningTo(encodeURIComponent(consent)), true],
    [loginReturningTo(loginReturningTo(consent)), true],
    ["/dashboard", false],
    ["/auth/jwt/register?onboarding_token=fixture", false],
    [loginReturningTo("/shared/fixture"), false],
    [loginReturningTo(`https://other.example.test${consent}`), false],
    [loginReturningTo("%2Fexample%"), false],
  ])(
    "records an immutable consent-only arrival decision for %s",
    (path, expected) => {
      const { window } = runHead(path);
      expect(
        Object.getOwnPropertyDescriptor(
          window,
          "__analyticsConsentHandoffAtLoad",
        ),
      ).toMatchObject({
        value: expected,
        writable: false,
        configurable: false,
      });
      window.history.replaceState(null, "", "/after-arrival");
      expect(window.__analyticsConsentHandoffAtLoad).toBe(expected);
    },
  );

  it("loads Tag Manager for ordinary registration and safe return destinations", () => {
    expect(
      googleUrls(
        taggedBuild,
        `${origin}/auth/jwt/register?returnTo=%2Fdashboard`,
      ),
    ).toEqual(["https://www.googletagmanager.com/gtm.js?id=GTM-TEST"]);
  });

  it("does not enable tags when callback cleanup makes the address eligible", () => {
    const { window, document } = runHead("/auth/jwt/login?saml_code=example");
    expect(window.location.href).toBe(`${origin}/auth/jwt/login`);
    expect(window.__analyticsUrlAllowed(window.location.href)).toBe(true);
    expect(window.__analyticsAllowedAtLoad).toBe(false);
    expect(googleUrlsIn(document)).toEqual([]);
  });

  it("rechecks the current address before Tag Manager starts", () => {
    const { window, document } = runHead("/dashboard", (_window, dom) => {
      dom.reconfigure({
        url: `${origin}/auth/jwt/register?onboarding_gcp_token=example`,
      });
    });
    expect(window.__analyticsAllowedAtLoad).toBe(true);
    expect(window.__analyticsUrlAllowed(window.location.href)).toBe(false);
    expect(googleUrlsIn(document)).toEqual([]);
  });

  it.each(["__authHandoffScrubFailed", "__authLateMarkerScrubFailed"])(
    "keeps tags off when address cleanup reports %s",
    (flag) => {
      const { window, document } = runHead("/dashboard", (page) => {
        page[flag] = true;
      });
      expect(window.__analyticsUrlAllowed(window.location.href)).toBe(false);
      expect(googleUrlsIn(document)).toEqual([]);
    },
  );

  it.each(["pushState", "replaceState"])(
    "uses a new document without a consent handoff for registration via %s",
    (method) => {
      const { window } = runHead("/auth/jwt/login");
      window.__prepareConsentIdentityHandoff = vi.fn();
      const target = "/auth/jwt/register?onboarding_token=example";
      window.history[method](null, "", target);
      expect(window.location.replace).toHaveBeenCalledWith(origin + target);
      expect(window.location.href).toBe(`${origin}/auth/jwt/login`);
      expect(window.__prepareConsentIdentityHandoff).not.toHaveBeenCalled();
    },
  );

  it("opens the root router's stored post-login destination in a fresh document", () => {
    const { dom, window } = runHead("/");
    dom.window.localStorage.setItem("redirectUrl", consent);
    const view = render(
      createElement(
        BrowserRouter,
        { window: dom.window },
        createElement(
          Routes,
          null,
          createElement(Route, {
            path: "/",
            element: createElement(Navigate, {
              to: dom.window.localStorage.getItem("redirectUrl"),
              replace: true,
            }),
          }),
          createElement(Route, {
            path: "/mcp/authorize",
            element: "Consent fixture",
          }),
        ),
      ),
    );
    expect(window.location.replace).toHaveBeenCalledWith(origin + consent);
    expect(window.location.href).toBe(`${origin}/`);
    expect(view.queryByText("Consent fixture")).toBeNull();
  });

  it.each([
    ["pushState", consent],
    ["pushState", loginReturningTo(consent)],
    ["replaceState", loginReturningTo(encodeURIComponent(consent))],
    ["replaceState", loginReturningTo("%2Fexample%")],
  ])("keeps %s tag-free for %s", (method, target) => {
    const { window } = runHead("/dashboard");
    window.history[method]({ step: 1 }, "", target);
    expect(window.location.replace).toHaveBeenCalledWith(origin + target);
    expect(window.location.href).toBe(`${origin}/dashboard`);
    const arrival = runHead(window.location.replace.mock.calls[0][0]);
    expect(arrival.window.__analyticsAllowedAtLoad).toBe(false);
    expect(googleUrlsIn(arrival.document)).toEqual([]);
  });

  it("restores the previous replace state and cancels the handoff if navigation throws", () => {
    const { window } = runHead("/");
    window.history.replaceState({ previous: true }, "", "/");
    const cancel = vi.fn(() => {
      throw new Error("Cleanup unavailable");
    });
    window.__prepareConsentIdentityHandoff = vi.fn(() => cancel);
    window.location.replace.mockImplementationOnce(() => {
      expect(window.__prepareConsentIdentityHandoff).toHaveBeenCalledOnce();
      throw new Error("Navigation unavailable");
    });
    expect(() =>
      window.history.replaceState({ next: true }, "", consent),
    ).toThrow("Navigation unavailable");
    expect(cancel.mock.calls).toEqual([[]]);
    expect(window.history.state).toEqual({ previous: true });
    expect(window.location.href).toBe(`${origin}/`);
  });

  it("does not let a failed analytics handoff block navigation", () => {
    const { window } = runHead("/");
    window.__prepareConsentIdentityHandoff = () => {
      throw new Error("Unavailable");
    };
    window.history.replaceState(null, "", loginReturningTo(consent));
    expect(window.location.replace).toHaveBeenCalledWith(
      origin + loginReturningTo(consent),
    );
  });

  it.each(["pushState", "replaceState"])(
    "guards URL objects from another realm via %s",
    (method) => {
      const { dom, window } = runHead("/dashboard");
      const { history } = window;
      // The ordinary call still goes through; only the guarded one gets a document.
      history[method]({ old: true }, "", new dom.window.URL("/before", origin));
      expect(window.location.href).toBe(`${origin}/before`);
      expect(window.location.replace).not.toHaveBeenCalled();
      const previousLength = history.length;
      expect(
        history[method](
          { next: true },
          "",
          new dom.window.URL(consent, origin),
        ),
      ).toBeUndefined();
      expect(history.length).toBe(
        previousLength + (method === "pushState" ? 1 : 0),
      );
      expect(window.location.href).toBe(`${origin}/before`);
      expect(window.location.replace).toHaveBeenCalledWith(origin + consent);
    },
  );

  it.each(["pushState", "replaceState"])(
    "validates storage state before any handoff via %s",
    async (method) => {
      const { window } = runHead(
        "/before",
        undefined,
        taggedBuild,
        serializeFixtureState,
      );
      const { history } = window;
      history.replaceState({ old: true }, "", "/before");
      history.pushState({ forward: true }, "", "/forward");
      history.back();
      await vi.waitFor(() => expect(history.state).toEqual({ old: true }));
      window.__prepareConsentIdentityHandoff = vi.fn();
      expect(() =>
        history[method]({ nested: [() => {}] }, "", new URL(consent, origin)),
      ).toThrow(expect.objectContaining({ name: "DataCloneError" }));
      expect(window.__prepareConsentIdentityHandoff).not.toHaveBeenCalled();
      expect(window.location.replace).not.toHaveBeenCalled();
      expect(window.location.assign).not.toHaveBeenCalled();
      expect(window.location.href).toBe(`${origin}/before`);
      expect(history.state).toEqual({ old: true });
      expect(history.length).toBe(2);
      history.forward();
      await vi.waitFor(() => expect(history.state).toEqual({ forward: true }));
    },
  );

  it.each(["pushState", "replaceState"])(
    "performs caller conversions once and in order via %s",
    (method) => {
      const { window } = runHead(
        "/dashboard",
        undefined,
        taggedBuild,
        serializeFixtureState,
      );
      const calls = [];
      const text = (name, value) => ({
        [Symbol.toPrimitive](hint) {
          calls.push(`${name}:${hint}`);
          return value;
        },
      });
      const state = {
        get step() {
          calls.push("state");
          return 1;
        },
      };
      window.__prepareConsentIdentityHandoff = () => {
        calls.push("prepare");
      };
      window.location.replace.mockImplementation(() => {
        calls.push("navigate");
      });
      window.history[method](state, text("title", ""), text("url", consent));
      expect(calls).toEqual([
        "title:string",
        "url:string",
        "state",
        "prepare",
        "navigate",
      ]);
      expect(window.location.href).toBe(`${origin}/dashboard`);
    },
  );

  it.each(["pushState", "replaceState"])(
    "preserves receiver, title and nullable URL semantics via %s",
    (method) => {
      const { window } = runHead("/before");
      const toString = vi.fn(() => consent);
      const value = { toString };
      const update = window.history[method];
      expect(() => update.call({}, null, value, value)).toThrow();
      expect(() =>
        update.call(window.history, null, Symbol("title"), value),
      ).toThrow();
      expect(() =>
        update.call(window.history, null, "", Symbol("url")),
      ).toThrow();
      expect(toString).not.toHaveBeenCalled();
      for (const url of [null, undefined]) {
        window.history[method]({ updated: true }, "", url);
        expect(window.location.href).toBe(`${origin}/before`);
        expect(window.history.state).toEqual({ updated: true });
      }
      expect(window.location.replace).not.toHaveBeenCalled();
    },
  );

  it("keeps the pre-push entry and its state when staging a guarded slot", async () => {
    const { window } = runHead("/before");
    const { history } = window;
    history.replaceState({ before: true }, "", "/before");
    history.pushState({ slot: true }, "", consent);
    expect(window.location.replace).toHaveBeenCalledWith(origin + consent);
    history.back();
    await vi.waitFor(() => expect(history.state).toEqual({ before: true }));
    expect(window.location.href).toBe(`${origin}/before`);
    history.forward();
    await vi.waitFor(() => expect(history.state).toEqual({ slot: true }));
    // The stub never commits the replacement document, so the safe slot is visible.
    expect(window.location.href).toBe(`${origin}/before`);
  });

  it("does not turn a guarded fragment-only replacement into an in-place hash change", () => {
    const { window } = runHead("/auth/jwt/register");
    window.history.replaceState(null, "", "#onboarding_token=fixture");
    const [target] = window.location.replace.mock.calls[0];
    expect(target).toBe(`${origin}/auth/jwt/register#onboarding_token=fixture`);
    expect(window.location.href.split("#")[0]).not.toBe(target.split("#")[0]);
    expect(window.location.href).not.toContain("onboarding_token");
  });

  it("starts a fresh document on traversal into guarded registration", () => {
    const { window, dom } = runHead("/dashboard");
    window.__prepareConsentIdentityHandoff = vi.fn();
    dom.reconfigure({
      url: `${origin}/auth/jwt/register?onboarding_token=example`,
    });
    dom.window.dispatchEvent(new dom.window.PopStateEvent("popstate"));
    expect(window.location.reload).toHaveBeenCalledOnce();
    expect(window.__prepareConsentIdentityHandoff).not.toHaveBeenCalled();
  });

  it.each([consent, loginReturningTo(encodeURIComponent(consent))])(
    "starts fresh before routing back or forward to %s",
    async (target) => {
      // Create the entries before tags start, then traverse with tags active.
      const { window, dom } = runHead("/first", ({ history }) => {
        history.pushState({ step: 1 }, "", target);
        history.pushState({ step: 2 }, "", "/last");
      });
      const laterListener = vi.fn();
      window.__prepareConsentIdentityHandoff = vi.fn();
      dom.window.addEventListener("popstate", laterListener);
      window.history.back();
      await vi.waitFor(() =>
        expect(window.location.reload).toHaveBeenCalledTimes(1),
      );
      expect(window.location.href).toBe(origin + target);
      expect(laterListener).not.toHaveBeenCalled();

      window.history.back();
      await vi.waitFor(() => expect(laterListener).toHaveBeenCalledTimes(1));
      expect(window.location.href).toBe(`${origin}/first`);

      window.history.forward();
      await vi.waitFor(() =>
        expect(window.location.reload).toHaveBeenCalledTimes(2),
      );
      expect(window.location.href).toBe(origin + target);
      expect(laterListener).toHaveBeenCalledTimes(1);
      expect(window.__prepareConsentIdentityHandoff.mock.calls).toEqual([
        [],
        [],
      ]);
    },
  );

  it.each([
    ["/", {}],
    [loginReturningTo(consent), taggedBuild],
  ])("keeps consent in-app when no tag started on %s", (start, env) => {
    const { window } = runHead(start, undefined, env);
    window.history.replaceState({ step: 1 }, "", consent);
    expect(window.location.href).toBe(origin + consent);
    expect(window.history.state).toEqual({ step: 1 });
    expect(window.location.replace).not.toHaveBeenCalled();
  });

  it.each([
    ["pushState", "/auth/jwt/register"],
    ["replaceState", loginReturningTo("/mcp/authorize-help")],
  ])("preserves ordinary %s navigation to %s", (method, target) => {
    const { window } = runHead("/auth/jwt/login");
    window.history[method]({ step: 1 }, "", target);
    expect(window.location.href).toBe(origin + target);
    expect(window.history.state).toEqual({ step: 1 });
    expect(window.location.assign).not.toHaveBeenCalled();
    expect(window.location.replace).not.toHaveBeenCalled();
  });

  it("keeps marketplace routing in-app in a document that never started tags", () => {
    const { window } = runHead("/auth/jwt/login?onboarding_gcp_token=example");
    const target = "/auth/jwt/register?onboarding_gcp_token=example";
    window.history.replaceState(null, "", target);
    expect(window.location.href).toBe(origin + target);
    expect(window.location.replace).not.toHaveBeenCalled();
  });

  it("keeps ordinary OSS history behavior when analytics is unconfigured", () => {
    const { window } = runHead("/auth/jwt/login", undefined, {});
    const target = "/auth/jwt/register?onboarding_token=example";
    window.history.pushState({ step: 1 }, "", target);
    expect(window.location.href).toBe(origin + target);
    expect(window.history.state).toEqual({ step: 1 });
    expect(window.location.assign).not.toHaveBeenCalled();
  });

  it("leaves reCAPTCHA to the app when the build sets a site key", () => {
    expect(googleUrls({ VITE_GOOGLE_SITE_KEY: "site-key" })).toEqual([]);
  });
});
