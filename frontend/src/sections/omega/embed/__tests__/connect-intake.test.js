/* eslint-env node */
import fs from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearOmegaConnect,
  hasOmegaConnectToken,
  onOmegaConnect,
  peekOmegaConnect,
  resetOmegaConnectForTests,
  takeOmegaConnectToken,
} from "../connect-intake";

// The real first script of frontend/index.html, run as the browser would.
const INDEX_HTML = fs.readFileSync(
  path.resolve(__dirname, "../../../../../index.html"),
  "utf8",
);
const parsed = new DOMParser().parseFromString(INDEX_HTML, "text/html");
const intake = parsed.querySelector("script[data-omega-connect-intake]");

const runIntake = () => {
  // eslint-disable-next-line no-new-func
  new Function(intake.textContent)();
};

const TOKEN = "tok_9f2c-A_b";
// jsdom's own Web Storage. On Node 25+ the runtime's experimental
// globalThis.localStorage can shadow window.localStorage, so read jsdom's.
const jsdomWindow = () => globalThis.jsdom?.window ?? window;
const storages = () => [
  jsdomWindow().localStorage,
  jsdomWindow().sessionStorage,
];
const visit = (url) => window.history.replaceState(null, "", url);

describe("index.html connect intake", () => {
  beforeEach(() => {
    delete window.__omegaConnect;
    resetOmegaConnectForTests();
    storages().forEach((storage) => storage.clear());
  });

  afterEach(() => {
    delete window.__omegaConnect;
    visit("/");
  });

  it("is the first script on the page, ahead of Google Tag Manager", () => {
    const scripts = [...parsed.querySelectorAll("script")];
    expect(scripts[0]).toBe(intake);
    const gtm = scripts.findIndex((s) =>
      s.textContent.includes("googletagmanager"),
    );
    expect(gtm).toBeGreaterThan(0);
    // Nothing that loads or runs code comes before it in <head>.
    const before = [...parsed.head.children].slice(
      0,
      [...parsed.head.children].indexOf(intake),
    );
    expect(before.map((el) => el.tagName)).toEqual(["META", "META"]);
    expect(before[0].getAttribute("charset")).toBe("utf-8");
    expect(before[0].attributes).toHaveLength(1);
    expect(before[1].httpEquiv).toBe("x-dns-prefetch-control");
    expect(before[1].content).toBe("off");
    expect(before[1].attributes).toHaveLength(2);
  });

  it("removes connect and t from the address bar and holds them once in memory", () => {
    visit(`/dashboard/studio?x=1#connect=http://127.0.0.1:4190&t=${TOKEN}`);

    runIntake();

    expect(window.location.href).toBe(
      `${window.location.origin}/dashboard/studio?x=1`,
    );
    expect(window.location.hash).toBe("");
    expect(Object.keys(window)).not.toContain("__omegaConnect");
    expect(JSON.stringify(window.history.state)).not.toContain(TOKEN);
    expect(window.__omegaConnect).toEqual({
      url: "http://127.0.0.1:4190",
      token: TOKEN,
    });
    expect(window.__omegaConnect).toBeNull();
    storages().forEach((storage) => expect(storage.length).toBe(0));
    expect(document.cookie).not.toContain(TOKEN);
  });

  it("keeps other fragment parameters and decodes encoded values", () => {
    visit(
      `/dashboard/studio#tab=runs&connect=http%3A%2F%2F127.0.0.1%3A4190&t=a%2Bb`,
    );

    runIntake();

    expect(window.location.hash).toBe("#tab=runs");
    expect(window.__omegaConnect).toEqual({
      url: "http://127.0.0.1:4190",
      token: "a+b",
    });
  });

  it("still strips the fragment when a value cannot be decoded", () => {
    visit(`/dashboard/studio#connect=http://127.0.0.1:4190&t=%E0%A4%A`);

    runIntake();

    expect(window.location.hash).toBe("");
    expect(window.__omegaConnect).toBeNull();
  });

  it("takes a link that arrives by fragment navigation on an open page", () => {
    visit("/dashboard/studio");
    runIntake();
    expect(window.__omegaConnect).toBeUndefined();
    const announced = vi.fn();
    window.addEventListener("omega:connect", announced);

    // The address bar changes without a page load; the browser fires these.
    visit(`/dashboard/studio#connect=http://127.0.0.1:4190&t=${TOKEN}`);
    window.dispatchEvent(new PopStateEvent("popstate"));
    window.dispatchEvent(new HashChangeEvent("hashchange"));

    window.removeEventListener("omega:connect", announced);
    expect(window.location.hash).toBe("");
    expect(announced).toHaveBeenCalledTimes(1);
    expect(window.__omegaConnect).toEqual({
      url: "http://127.0.0.1:4190",
      token: TOKEN,
    });
  });

  it("strips with the browser's own replaceState, unseen by wrappers added later", () => {
    visit("/dashboard/studio");
    const native = window.history.replaceState;
    runIntake();
    // An analytics SDK wraps history after the intake has run.
    const seen = [];
    window.history.replaceState = function (...args) {
      seen.push(window.location.href, String(args[2]));
      return native.apply(this, args);
    };
    try {
      visit(`/dashboard/studio#connect=http://127.0.0.1:4190&t=${TOKEN}`);
      seen.length = 0; // the test's own navigation above went through the wrapper
      window.dispatchEvent(new PopStateEvent("popstate"));
    } finally {
      window.history.replaceState = native;
    }
    expect(window.location.hash).toBe("");
    expect(seen).toEqual([]);
  });

  it("leaves unrelated fragments alone", () => {
    visit("/dashboard/observe#t=12&section=2");

    runIntake();

    expect(window.location.hash).toBe("#t=12&section=2");
    expect(window.__omegaConnect).toBeUndefined();
  });

  it("strips a connect link with no token and holds nothing", () => {
    visit("/dashboard/studio#connect=http://127.0.0.1:4190");

    runIntake();

    expect(window.location.hash).toBe("");
    expect(window.__omegaConnect).toBeNull();
  });
});

describe("connect-intake module", () => {
  beforeEach(() => {
    delete window.__omegaConnect;
    resetOmegaConnectForTests();
  });

  it("hands the token out exactly once and never shows it in peek", () => {
    visit(`/dashboard/studio#connect=http://127.0.0.1:4190/&t=${TOKEN}`);
    runIntake();

    expect(peekOmegaConnect()).toEqual({
      url: "http://127.0.0.1:4190",
      origin: "http://127.0.0.1:4190",
    });
    expect(window.__omegaConnect).toBeUndefined(); // moved off window on first use
    expect(hasOmegaConnectToken()).toBe(true);
    expect(takeOmegaConnectToken()).toBe(TOKEN);
    expect(takeOmegaConnectToken()).toBeNull();
    expect(hasOmegaConnectToken()).toBe(false);
    expect(peekOmegaConnect()).toEqual({
      url: "http://127.0.0.1:4190",
      origin: "http://127.0.0.1:4190",
    });
  });

  it("cannot recover a spent handoff after reload; only a fresh fragment supplies another", () => {
    const server = "http://127.0.0.1:4190";
    visit(`/dashboard/studio#connect=${server}&t=${TOKEN}`);
    runIntake();
    expect(takeOmegaConnectToken()).toBe(TOKEN);
    expect(takeOmegaConnectToken()).toBeNull();
    expect(window.location.hash).toBe("");

    // A host reload starts with only the scrubbed URL. No intake value or
    // memory left over from the spent handoff can supply another key.
    resetOmegaConnectForTests();
    runIntake();
    expect(peekOmegaConnect()).toBeNull();
    expect(hasOmegaConnectToken()).toBe(false);
    expect(takeOmegaConnectToken()).toBeNull();

    visit(`/dashboard/studio#connect=${server}&t=fresh-link-key`);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    expect(hasOmegaConnectToken()).toBe(true);
    expect(takeOmegaConnectToken()).toBe("fresh-link-key");
    expect(takeOmegaConnectToken()).toBeNull();
    expect(window.location.hash).toBe("");
    for (const storage of storages()) {
      expect(JSON.stringify({ ...storage })).not.toContain(TOKEN);
      expect(JSON.stringify({ ...storage })).not.toContain("fresh-link-key");
    }
  });

  it("rejects links that are not http(s)", () => {
    visit(`/dashboard/studio#connect=javascript:alert(1)&t=${TOKEN}`);
    runIntake();

    expect(peekOmegaConnect()).toBeNull();
    expect(takeOmegaConnectToken()).toBeNull();
  });

  it("returns nothing when no link was opened", () => {
    expect(peekOmegaConnect()).toBeNull();
    expect(takeOmegaConnectToken()).toBeNull();
  });

  it("replaces a waiting link with a newer one and tells subscribers", () => {
    visit(`/dashboard/studio#connect=http://127.0.0.1:4190&t=first`);
    runIntake();
    expect(peekOmegaConnect().url).toBe("http://127.0.0.1:4190");
    const seen = [];
    const stop = onOmegaConnect((value) => seen.push(value));

    visit(`/dashboard/studio#connect=http://127.0.0.1:4200&t=second`);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    stop();

    expect(seen).toEqual([
      { url: "http://127.0.0.1:4200", origin: "http://127.0.0.1:4200" },
    ]);
    expect(takeOmegaConnectToken()).toBe("second");
  });

  it("forgets the link on clear", () => {
    visit(`/dashboard/studio#connect=http://127.0.0.1:4190&t=${TOKEN}`);
    runIntake();
    clearOmegaConnect();

    expect(peekOmegaConnect()).toBeNull();
    expect(takeOmegaConnectToken()).toBeNull();
  });
});
