import { describe, expect, it } from "vitest";
import {
  emptyListText,
  hasStoredCredential,
  isMCPEnabled,
  isMCPOffError,
  isMCPTurnedOff,
  stripSecrets,
} from "./utils";

describe("hasStoredCredential", () => {
  it.each([
    ["an inline bearer token", { auth: { type: "bearer", token: "x" } }, true],
    ["an inline API key", { auth: { type: "api_key", key: "x" } }, true],
    ["an auth type with no value", { auth: { type: "bearer" } }, false],
    [
      "an active vault credential",
      { credential: { mode: "shared", credential_id: "c1", state: "active" } },
      true,
    ],
    [
      "a vault credential with no state",
      { credential: { mode: "shared", credential_id: "c1" } },
      true,
    ],
    [
      "a revoked vault credential",
      { credential: { mode: "shared", credential_id: "c1", state: "revoked" } },
      false,
    ],
    ["credential mode none", { credential: { mode: "none" } }, false],
    ["a pushed secret", { credential: { secret: { value: "x" } } }, true],
    ["no credential", { url: "https://x" }, false],
    ["nothing", undefined, false],
  ])("%s", (_, config, expected) => {
    expect(hasStoredCredential(config)).toBe(expected);
  });
});

describe("stripSecrets", () => {
  it("drops every secret value and keeps the rest", () => {
    const stored = {
      url: "https://x",
      transport: "http",
      auth: { type: "api_key", header: "X-Key", key: "k", token: "t" },
      credential: {
        mode: "shared",
        credential_id: "c1",
        secret: { value: "s" },
      },
    };

    expect(stripSecrets(stored)).toEqual({
      url: "https://x",
      transport: "http",
      auth: { type: "api_key", header: "X-Key" },
      credential: { mode: "shared", credential_id: "c1" },
    });
    expect(stored.auth.key).toBe("k");
  });

  it("returns an empty object for nothing", () => {
    expect(stripSecrets(undefined)).toEqual({});
  });
});

describe("isMCPEnabled", () => {
  it.each([
    [{ enabled: true }, true],
    [{ enabled: "true" }, false],
    [{ enabled: 1 }, false],
    [{ enabled: false }, false],
    [{}, false],
    [undefined, false],
  ])("%j is %s", (status, expected) => {
    expect(isMCPEnabled(status)).toBe(expected);
  });
});

describe("isMCPTurnedOff", () => {
  it.each([
    [{ enabled: false, reason: "gateway_mcp_disabled" }, true],
    // An outage or an org with no server is not "MCP is off".
    [{ enabled: false, reason: "gateway_unreachable" }, false],
    [{ enabled: false }, false],
    [{ enabled: true, reason: "gateway_mcp_disabled" }, false],
    [undefined, false],
  ])("%j is %s", (status, expected) => {
    expect(isMCPTurnedOff(status)).toBe(expected);
  });
});

describe("isMCPOffError", () => {
  it.each([
    [{ code: "gateway_mcp_disabled", statusCode: 404 }, true],
    [{ code: "gateway_unreachable", statusCode: 502 }, false],
    [{ code: "not_found", statusCode: 404 }, false],
    [undefined, false],
  ])("%j is %s", (error, expected) => {
    expect(isMCPOffError(error)).toBe(expected);
  });
});

describe("emptyListText", () => {
  it("says the read failed before anything else", () => {
    expect(emptyListText(true, [], "tools", "none", "no match")).toBe(
      "MCP tools could not be loaded from the gateway.",
    );
  });

  it("says there is nothing, or nothing matching", () => {
    expect(emptyListText(false, [], "tools", "none", "no match")).toBe("none");
    expect(emptyListText(false, [{}], "tools", "none", "no match")).toBe(
      "no match",
    );
  });
});
