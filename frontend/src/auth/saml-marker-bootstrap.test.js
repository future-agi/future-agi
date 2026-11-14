import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const axios = vi.hoisted(() => ({ defaults: { headers: { common: {} } } }));
vi.mock("src/utils/axios", () => ({ default: axios }));

import { bootstrapSamlSession } from "./saml-bootstrap";

const html = readFileSync("index.html", "utf8");
const initial =
  html.match(
    /<script id="saml-initial-marker-guard">([\s\S]*?)<\/script>/,
  )?.[1] ?? "";
const late =
  html.match(/<script id="saml-late-marker-guard">([\s\S]*?)<\/script>/)?.[1] ??
  "";
const nativePush = window.history.pushState;
const nativeReplace = window.history.replaceState;
let listeners;

function installGuards() {
  const context = { window, document, URL, Reflect };
  runInNewContext(initial, context);
  runInNewContext(late, context);
}

describe("callback markers before the SAML session bootstrap", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    localStorage.setItem("accessToken", "existing-access");
    localStorage.setItem("refreshToken", "existing-refresh");
    sessionStorage.setItem("organizationId", "existing-org");
    sessionStorage.setItem("workspaceId", "existing-workspace");
    axios.defaults.headers.common = { Authorization: "Bearer existing-access" };
    vi.stubGlobal("crypto", { randomUUID: () => "new-generation" });
    listeners = vi.spyOn(window, "addEventListener");
  });

  afterEach(() => {
    for (const [type, listener, options] of listeners.mock.calls) {
      window.removeEventListener(type, listener, options);
    }
    listeners.mockRestore();
    window.history.pushState = nativePush;
    window.history.replaceState = nativeReplace;
    window.history.replaceState(null, "", "/");
    delete window.__takeSamlUrlSanitizer;
    vi.unstubAllGlobals();
  });

  it.each(["&saml_code=example", "#saml_code=example", "#%73aml_code=example"])(
    "retains the stored session for an initial mixed callback %s",
    (marker) => {
      window.history.replaceState(
        { retained: true },
        "",
        `/auth/jwt/login?auth=saml&sso_token=other-access${marker}`,
      );
      installGuards();

      expect(bootstrapSamlSession()).toBe(false);
      expect(window.location.search).toBe("");
      expect(window.location.hash).toBe("");
      expect(window.history.state).toEqual({ retained: true });
      expect(localStorage.getItem("accessToken")).toBe("existing-access");
      expect(localStorage.getItem("refreshToken")).toBe("existing-refresh");
      expect(sessionStorage.getItem("organizationId")).toBe("existing-org");
      expect(sessionStorage.getItem("workspaceId")).toBe("existing-workspace");
      expect(axios.defaults.headers.common.Authorization).toBe(
        "Bearer existing-access",
      );
    },
  );

  it("retains the unmarked session bootstrap with the history guards installed", () => {
    window.history.replaceState(
      null,
      "",
      "/auth/jwt/login?auth=saml&sso_token=new-access&next=%2Fdashboard%2Fobserve",
    );
    installGuards();

    expect(bootstrapSamlSession()).toBe(true);
    expect(localStorage.getItem("accessToken")).toBe("new-access");
    expect(localStorage.getItem("refreshToken")).toBeNull();
    expect(sessionStorage.getItem("organizationId")).toBeNull();
    expect(sessionStorage.getItem("workspaceId")).toBeNull();
    expect(sessionStorage.getItem("fai_saml_next")).toBe("/dashboard/observe");
    expect(axios.defaults.headers.common.Authorization).toBe(
      "Bearer new-access",
    );
    expect(window.location.search).toBe("");
  });
});
