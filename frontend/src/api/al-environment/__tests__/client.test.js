import { describe, it, expect } from "vitest";
import { alkBaseUrl, isDirectToHarness, applyAuth, ALK_PROXY_PATH } from "../client";

describe("alkBaseUrl", () => {
  it("defaults to the proxy on the API host, which is a different origin from ours", () => {
    expect(alkBaseUrl({}, "http://localhost:8000")).toBe(
      "http://localhost:8000/simulate/harness"
    );
    expect(alkBaseUrl({}, "https://dev.api.futureagi.com")).toBe(
      "https://dev.api.futureagi.com/simulate/harness"
    );
    expect(ALK_PROXY_PATH).toBe("/simulate/harness");
  });

  it("can be pointed straight at a local harness for development", () => {
    expect(alkBaseUrl({ VITE_ALK_API_BASE: "http://localhost:8777/api" })).toBe(
      "http://localhost:8777/api"
    );
  });

  it("strips a trailing slash so paths do not double up", () => {
    expect(alkBaseUrl({ VITE_ALK_API_BASE: "http://localhost:8777/api/" })).toBe(
      "http://localhost:8777/api"
    );
  });

  it("ignores a blank value rather than producing an empty base", () => {
    expect(alkBaseUrl({ VITE_ALK_API_BASE: "   " }, "http://localhost:8000")).toBe(
      "http://localhost:8000/simulate/harness"
    );
  });
});

describe("isDirectToHarness", () => {
  it("recognises a base that bypasses our backend", () => {
    expect(isDirectToHarness("http://localhost:8777/api")).toBe(true);
    expect(isDirectToHarness("https://harness.internal/api")).toBe(true);
  });

  it("treats the proxy as going through our backend, absolute or not", () => {
    expect(isDirectToHarness("/simulate/harness")).toBe(false);
    // The one that matters: the real default is absolute, and reading the scheme would strip
    // the auth headers off every proxied call.
    expect(isDirectToHarness("http://localhost:8000/simulate/harness")).toBe(false);
    expect(isDirectToHarness("https://dev.api.futureagi.com/simulate/harness")).toBe(false);
  });
});

describe("applyAuth", () => {
  const shared = {
    Authorization: "Bearer token",
    "X-Organization-Id": "org-1",
    "X-Workspace-Id": "ws-1",
  };

  it("authenticates proxied calls the same way as any other /simulate/ call", () => {
    const config = { headers: {} };
    applyAuth(config, "/simulate/harness", shared);
    expect(config.headers).toEqual(shared);
  });

  it("sends nothing to a harness reached directly, which has no auth", () => {
    const config = { headers: {} };
    applyAuth(config, "http://localhost:8777/api", shared);
    expect(config.headers).toEqual({});
  });

  it("omits headers the app has not set yet", () => {
    const config = { headers: {} };
    applyAuth(config, "/simulate/harness", { Authorization: "Bearer token" });
    expect(config.headers).toEqual({ Authorization: "Bearer token" });
  });
});
