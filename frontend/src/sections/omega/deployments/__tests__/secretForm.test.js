import { describe, expect, it } from "vitest";

import { OmegaError } from "src/sections/omega/shared/omegaStates";

import {
  activeNames,
  addSecretProblems,
  afterFailure,
  canManageSecretsFor,
  kindLabel,
  secretNameProblem,
  secretValueProblem,
  usageLabel,
} from "../lib/secretForm";

describe("secret form rules (EE credential-vault.ts + deployment injection names)", () => {
  it("accepts only names a deployment can inject, and none twice", () => {
    expect(secretNameProblem("OPENAI_API_KEY")).toBeNull();
    expect(secretNameProblem("_PRIVATE")).toBeNull();
    expect(secretNameProblem("")).toMatch(/Name the secret/);
    for (const bad of [
      "openai_key",
      "1KEY",
      "MY-KEY",
      "MY KEY",
      "KEY.",
      "K".repeat(129),
    ]) {
      expect(secretNameProblem(bad), bad).toMatch(/A-Z, 0-9 and _/);
    }
    expect(secretNameProblem("K".repeat(128))).toBeNull();
    expect(secretNameProblem("OMEGA_SERVE_TOKEN")).toMatch(
      /set by the deployment itself/,
    );
    expect(
      secretNameProblem("OPENAI_API_KEY", new Set(["OPENAI_API_KEY"])),
    ).toMatch(/already in the vault\. Replace its value instead/);
  });

  it("takes a value exactly as typed, up to EE's 16 KB, never empty or with NUL", () => {
    expect(secretValueProblem("  sk-with-spaces  ")).toBeNull();
    expect(secretValueProblem("")).toMatch(/Enter the value/);
    expect(secretValueProblem("a\0b")).toMatch(/NUL/);
    expect(secretValueProblem("x".repeat(16 * 1024))).toBeNull();
    expect(secretValueProblem("x".repeat(16 * 1024 + 1))).toMatch(/16 KB/);
    // Bytes, not characters: 8,193 two-byte characters are over the cap.
    expect(secretValueProblem("é".repeat(8193))).toMatch(/16 KB/);
  });

  it("refuses a kind EE does not know, and is ok only when every field is", () => {
    const good = { name: "A_KEY", kind: "api_key", value: "v" };
    expect(addSecretProblems(good, new Set()).ok).toBe(true);
    expect(addSecretProblems({ ...good, kind: "generic" }, new Set()).ok).toBe(
      true,
    );
    expect(
      addSecretProblems({ ...good, kind: "password" }, new Set()).kindError,
    ).toMatch(/Pick a kind/);
    expect(addSecretProblems({ ...good, value: "" }, new Set()).ok).toBe(false);
    expect(addSecretProblems(good, new Set(["A_KEY"])).ok).toBe(false);
  });

  it("counts only active names as taken", () => {
    const names = activeNames([
      { name: "A", status: "active" },
      { name: "B", status: "revoked" },
    ]);
    expect([...names]).toEqual(["A"]);
  });

  it("lets organization Owners and Admins manage secrets, nobody else", () => {
    expect(canManageSecretsFor("Owner")).toBe(true);
    expect(canManageSecretsFor("Admin")).toBe(true);
    for (const role of [
      "Member",
      "Viewer",
      "workspace_admin",
      "workspace_member",
      "workspace_viewer",
      undefined,
      null,
    ]) {
      expect(canManageSecretsFor(role), String(role)).toBe(false);
    }
  });

  it("says what a failed write left behind", () => {
    const copy = { refused: "nothing changed", unknown: "maybe" };
    expect(
      afterFailure(new OmegaError({ state: "error", status: 400 }), copy),
    ).toBe("nothing changed");
    expect(
      afterFailure(new OmegaError({ state: "refused", status: 403 }), copy),
    ).toBe("nothing changed");
    expect(
      afterFailure(new OmegaError({ state: "unavailable", status: 503 }), copy),
    ).toBe("maybe");
  });

  it("labels use and kinds without claiming more than the record says", () => {
    expect(usageLabel(0)).toBe("Not used yet");
    expect(usageLabel(1)).toBe("Used in 1 place");
    expect(usageLabel(3)).toBe("Used in 3 places");
    expect(kindLabel("api_key")).toBe("API key");
    expect(kindLabel("generic")).toBe("Other secret");
    expect(kindLabel("oauth_token")).toBe("OAuth token");
  });
});
