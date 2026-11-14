import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { describe, expect, it } from "vitest";

const html = readFileSync("index.html", "utf8");
const initial =
  html.match(
    /<script id="saml-initial-marker-guard">([\s\S]*?)<\/script>/,
  )?.[1] ?? "";
const late =
  html.match(/<script id="saml-late-marker-guard">([\s\S]*?)<\/script>/)?.[1] ??
  "";
const code = "b".repeat(43);

function environment(
  url = "https://ui.owned.invalid/dashboard/?keep=1",
  initialFailure = false,
) {
  let current = new URL(url);
  const calls = [];
  const listeners = {};
  let replaceFailure = initialFailure;
  const history = { state: { retained: true } };
  for (const method of ["pushState", "replaceState"]) {
    history[method] = function (...args) {
      if (this !== history) throw new TypeError("Illegal invocation");
      if (method === "replaceState" && replaceFailure)
        throw new DOMException("Owned platform failure", "SecurityError");
      if (args.length < 2) throw new TypeError("Missing arguments");
      if (typeof args[0] === "function")
        throw new DOMException("Uncloneable", "DataCloneError");
      const target =
        args.length < 3 || args[2] === undefined || args[2] === null
          ? current
          : new URL(String(args[2]), current);
      if (target.origin !== current.origin)
        throw new DOMException("Other origin URL", "SecurityError");
      calls.push({ method, args });
      history.state = args[0];
      current = target;
    };
  }
  const native = { push: history.pushState, replace: history.replaceState };
  const window = {
    history,
    get location() {
      return current;
    },
    addEventListener(name, listener, capture) {
      listeners[name] = { listener, capture };
    },
  };
  const document = {
    get baseURI() {
      return current.href;
    },
  };
  const context = { window, document, URL, Reflect };
  runInNewContext(initial, context);
  runInNewContext(late, context);
  return {
    window,
    history,
    calls,
    native,
    listeners,
    context,
    failNativeReplace() {
      replaceFailure = true;
    },
    setURL(value) {
      current = new URL(value, current);
    },
  };
}

describe("late SAML URL marker handling", () => {
  it("keeps optional-font styles outside the guarded application import", () => {
    const stylesheet = '<link rel="stylesheet" href="/src/global.css" />';
    expect(html).toContain(stylesheet);
    expect(html.indexOf(stylesheet)).toBeGreaterThan(
      html.indexOf('window.addEventListener("popstate", onNavigation, true)'),
    );
    expect(readFileSync("src/app.jsx", "utf8")).not.toContain(
      'import "src/global.css"',
    );
  });

  it("does not load legacy consumers if initial replacement and navigation fail", () => {
    const env = environment(
      "https://ui.owned.invalid/auth/jwt/login?auth=saml&sso_token=wrong#saml_code=" +
        code,
      true,
    );
    expect(env.window.__authHandoffScrubFailed).toBe(true);
    expect(env.window.location.hash).toBe("#saml_code=" + code);
    expect(env.window.__takeSamlHandoff).toBeUndefined();
    let loads = 0;
    env.window.loadMain = () => {
      loads++;
    };
    const entry = readFileSync("src/entry.js", "utf8");
    expect(html).toContain('src="/src/entry.js"');
    expect(entry).toContain('import("./main.jsx")');
    // Stub only the module-loader boundary; execute the shipped entry guard.
    const guardedEntry = entry.replace(
      'import("./main.jsx")',
      "window.loadMain()",
    );
    runInNewContext(guardedEntry, env.context);
    expect(loads).toBe(0);
    delete env.window.__authHandoffScrubFailed;
    runInNewContext(guardedEntry, env.context);
    expect(loads).toBe(1);
  });

  it.each([
    "https://ui.owned.invalid/auth/jwt/login#saml_code=" + code,
    "https://ui.owned.invalid/dashboard/?keep=1&saml_code=" + code,
    "https://ui.owned.invalid/auth/jwt/login?auth=saml&sso_token=wrong#saml_code=" +
      code,
    "https://ui.owned.invalid/auth/jwt/login#%73aml_code=" + code,
  ])("removes initial sign-in code without creating a consumer: %s", (url) => {
    const env = environment(url);
    expect(env.window.location.href).not.toContain(code);
    expect(env.window.location.href).not.toContain("sso_token");
    expect(env.window.location.hash).toBe("");
    expect(env.window.__takeSamlHandoff).toBeUndefined();
    expect(env.window.__takeOAuthHandoff).toBeUndefined();
    expect(env.history.state).toEqual({ retained: true });
  });

  it("leaves the existing unmarked legacy transport unchanged", () => {
    const url =
      "https://ui.owned.invalid/auth/jwt/login?auth=saml&sso_token=legacy&next=/dashboard/";
    const env = environment(url);
    expect(env.window.location.href).toBe(url);
    expect(env.calls).toEqual([]);
  });

  it("installs before analytics and shares the initial URL sanitizer", () => {
    const env = environment();
    const appEntry = html.indexOf('src="/src/entry.js"');
    expect(appEntry).toBeGreaterThan(0);
    expect(html.indexOf('id="saml-late-marker-guard"')).toBeLessThan(appEntry);
    expect(env.window.__takeSamlUrlSanitizer).toBeUndefined();
    expect(env.listeners.hashchange.capture).toBe(true);
    expect(env.listeners.popstate.capture).toBe(true);
  });

  it.each(["pushState", "replaceState"])(
    "scrubs marked same-origin %s synchronously without a handoff",
    (method) => {
      const env = environment();
      const state = { native: "state" };
      env.history[method](
        state,
        "",
        `/dashboard/?keep=1&sso_token=mixed#saml_code=${code}`,
      );
      expect(env.window.location.href).toBe(
        "https://ui.owned.invalid/dashboard/?keep=1",
      );
      expect(env.history.state).toBe(state);
      expect(env.window.__takeSamlHandoff).toBeUndefined();
      expect(env.window.__takeOAuthHandoff).toBeUndefined();
      expect(JSON.stringify(env.calls)).not.toContain(code);
    },
  );

  it.each(["pushState", "replaceState"])(
    "preserves ordinary %s arguments and errors",
    (method) => {
      const env = environment();
      env.history[method]({ page: 2 }, "", "#ordinary");
      expect(env.window.location.hash).toBe("#ordinary");
      expect(env.calls.at(-1).args[2]).toBe("#ordinary");
      expect(() =>
        env.history[method](
          {},
          "",
          `https://other-origin.invalid/#saml_code=${code}`,
        ),
      ).toThrowError(/Other origin URL/);
      expect(() =>
        env.history[method](() => {}, "", `#saml_code=${code}`),
      ).toThrowError(/Uncloneable/);
      expect(() => env.history[method]({})).toThrowError(/Missing arguments/);
      expect(env.window.location.hash).toBe("#ordinary");
      expect(env.window.__authHandoffScrubFailed).toBeUndefined();
    },
  );

  it("converts a URL object only once using the native call", () => {
    const env = environment();
    let conversions = 0;
    env.history.pushState({}, "", {
      toString() {
        conversions += 1;
        return `#saml_code=${code}`;
      },
    });
    expect(conversions).toBe(1);
    expect(env.window.location.hash).toBe("");
  });

  it("does not add a thrown error when object URL conversion succeeds but the later scrub fails", () => {
    const env = environment();
    let conversions = 0;
    env.failNativeReplace();
    expect(() =>
      env.history.pushState({ kept: true }, "", {
        toString() {
          conversions += 1;
          return `#saml_code=${code}`;
        },
      }),
    ).not.toThrow();
    expect(conversions).toBe(1);
    expect(env.window.__authLateMarkerScrubFailed).toBe(true);
    expect(env.history.state).toEqual({ kept: true });
    // Both history scrubbing and fallback navigation are unavailable in this
    // injected platform failure; do not claim URL removal in that case.
    expect(env.window.location.hash).toBe(`#saml_code=${code}`);
  });

  it("scrubs native hash navigation and suppresses its raw event URL", () => {
    const env = environment();
    const oldURL = env.window.location.href;
    env.setURL(`#saml_code=${code}`);
    let stopped = false;
    env.listeners.hashchange.listener({
      type: "hashchange",
      oldURL,
      newURL: env.window.location.href,
      stopImmediatePropagation() {
        stopped = true;
      },
    });
    expect(stopped).toBe(true);
    expect(env.window.location.hash).toBe("");
    expect(env.history.state).toEqual({ retained: true });
  });

  it("suppresses a queued marked event even after a prior event scrubbed the URL", () => {
    const env = environment();
    let stopped = false;
    env.listeners.hashchange.listener({
      type: "hashchange",
      oldURL: `https://ui.owned.invalid/#saml_code=${code}`,
      newURL: "https://ui.owned.invalid/#ordinary",
      stopImmediatePropagation() {
        stopped = true;
      },
    });
    expect(stopped).toBe(true);
    expect(env.window.location.pathname).toBe("/dashboard/");
  });

  it("suppresses a marked event even if the native scrub and fallback navigation fail", () => {
    const env = environment();
    const oldURL = env.window.location.href;
    env.setURL(`#saml_code=${code}`);
    env.failNativeReplace();
    let stopped = false;
    expect(() =>
      env.listeners.hashchange.listener({
        type: "hashchange",
        oldURL,
        newURL: env.window.location.href,
        stopImmediatePropagation() {
          stopped = true;
        },
      }),
    ).not.toThrow();
    expect(stopped).toBe(true);
    expect(env.window.__authLateMarkerScrubFailed).toBe(true);
    // This intentionally injected platform failure cannot claim URL removal.
    expect(env.window.location.hash).toBe(`#saml_code=${code}`);
    expect(env.history.state).toEqual({ retained: true });
    expect(env.window.__takeSamlHandoff).toBeUndefined();
  });

  it("leaves ordinary hash events untouched", () => {
    const env = environment();
    env.setURL("#ordinary");
    let stopped = false;
    env.listeners.hashchange.listener({
      type: "hashchange",
      oldURL: "https://ui.owned.invalid/",
      newURL: env.window.location.href,
      stopImmediatePropagation() {
        stopped = true;
      },
    });
    expect(stopped).toBe(false);
    expect(env.window.location.hash).toBe("#ordinary");
  });

  it("scrubs a marked history restoration without clearing native state", () => {
    const env = environment();
    env.setURL(`?keep=1&saml_code=${code}`);
    let stopped = false;
    env.listeners.popstate.listener({
      type: "popstate",
      stopImmediatePropagation() {
        stopped = true;
      },
    });
    expect(stopped).toBe(true);
    expect(env.window.location.search).toBe("?keep=1");
    expect(env.history.state).toEqual({ retained: true });
  });
});
