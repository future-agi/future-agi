import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const axios = vi.hoisted(() => ({
  defaults: {
    headers: {
      common: {},
    },
  },
}));

vi.mock("src/utils/axios", () => ({ default: axios }));

import {
  bootstrapSamlSession,
  isSafeSamlNext,
  SAML_AUTH_GENERATION_KEY,
} from "./saml-bootstrap";

describe("SAML bootstrap barrier", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    axios.defaults.headers.common = {
      Authorization: "Bearer old-token",
      "X-Organization-Id": "org-b",
      "X-Workspace-Id": "workspace-b",
    };
    vi.stubGlobal("crypto", { randomUUID: () => "saml-generation" });
    window.history.replaceState(null, "", "/dashboard/develop");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("replaces a prior browser session before providers can render", () => {
    localStorage.setItem("refreshToken", "old-refresh");
    localStorage.setItem("rememberMe", "true");
    sessionStorage.setItem("organizationId", "org-b");
    sessionStorage.setItem("workspaceId", "workspace-b");
    window.history.replaceState(
      null,
      "",
      "/?sso_token=saml-token&auth=saml&next=%2Fdashboard%2Fdevelop%2Fruns",
    );

    expect(bootstrapSamlSession()).toBe(true);

    expect(localStorage.getItem("accessToken")).toBe("saml-token");
    expect(localStorage.getItem("refreshToken")).toBeNull();
    expect(sessionStorage.getItem("organizationId")).toBeNull();
    expect(sessionStorage.getItem("workspaceId")).toBeNull();
    expect(sessionStorage.getItem(SAML_AUTH_GENERATION_KEY)).toBe(
      "saml-generation",
    );
    expect(sessionStorage.getItem("fai_saml_next")).toBe(
      "/dashboard/develop/runs",
    );
    expect(axios.defaults.headers.common).toEqual({
      Authorization: "Bearer saml-token",
    });
    expect(window.location.search).toBe("");
  });

  it("does not treat OAuth redirects as a SAML bootstrap", () => {
    window.history.replaceState(null, "", "/?sso_token=oauth-token");

    expect(bootstrapSamlSession()).toBe(false);
    expect(localStorage.getItem("accessToken")).toBeNull();
  });

  it.each(["//evil.example", "https://evil.example", "/safe\\evil", "x"]) (
    "rejects unsafe next path %s",
    (next) => {
      expect(isSafeSamlNext(next)).toBe(false);
    },
  );
});
