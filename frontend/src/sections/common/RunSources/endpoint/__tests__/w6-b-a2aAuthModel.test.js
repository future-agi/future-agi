// W6 B4: A2A auth kinds, each from a workspace secret.
import { describe, expect, it } from "vitest";
import {
  authFieldsOf,
  authHeaderOf,
  authIssues,
  authOf,
  authSecrets,
  authSummary,
  cardAuthKind,
} from "../a2aAuthModel";

const NAMES = ["ACME_AGENT_TOKEN", "FI_PROJECT_KEY"];

describe("a2aAuthModel", () => {
  it("reads the older header-from-secret pair", () => {
    expect(
      authOf({ name: "Authorization", secret: "ACME_AGENT_TOKEN" }),
    ).toEqual({
      kind: "bearer",
      secret: "ACME_AGENT_TOKEN",
    });
    expect(authOf({ name: "X-Api-Key", secret: "K" })).toEqual({
      kind: "api_key",
      header: "X-Api-Key",
      secret: "K",
    });
    expect(authOf(null)).toEqual({ kind: "none" });
    // The card's untouched default (a header with no secret) sends nothing.
    expect(authOf({ name: "Authorization", secret: "" })).toEqual({
      kind: "none",
    });
    expect(authHeaderOf({ name: "Authorization", secret: "" })).toBeNull();
  });

  it("masks the secret in the header it adds", () => {
    expect(
      authHeaderOf({ kind: "bearer", secret: "ACME_AGENT_TOKEN" }),
    ).toEqual({
      name: "Authorization",
      value: "Bearer •••• (from secret ACME_AGENT_TOKEN)",
    });
    expect(
      authHeaderOf({ kind: "api_key", header: "X-Key", secret: "S" }).name,
    ).toBe("X-Key");
    expect(
      authHeaderOf({ kind: "basic", username: "bot", secret: "S" }).value,
    ).toBe("Basic bot:•••• (from secret S)");
    expect(authHeaderOf({ kind: "none" })).toBeNull();
    // OAuth 2 sends the token its exchange answered, never a placeholder.
    const oauth = {
      kind: "oauth2",
      token_url: "https://auth.globex.test/oauth/token",
      client_id: "fi-experiments",
      secret: "FI_PROJECT_KEY",
    };
    expect(authHeaderOf(oauth)).toBeNull();
    const token = {
      name: "Authorization",
      value:
        "Bearer ••••1a2b (OAuth token from auth.globex.test, expires in 3600 s)",
    };
    expect(authHeaderOf(oauth, token)).toEqual(token);
    // A half-set OAuth card names only what it has.
    expect(authSummary({ kind: "oauth2", secret: "FI_PROJECT_KEY" })).toBe(
      "OAuth 2 client credentials · secret FI_PROJECT_KEY",
    );
    expect(authSummary(oauth)).toBe(
      "OAuth 2 client credentials via https://auth.globex.test/oauth/token (client fi-experiments) · secret FI_PROJECT_KEY",
    );
  });

  it("blocks a missing secret and incomplete kinds", () => {
    expect(
      authIssues({ kind: "bearer", secret: "ACME_AGENT_TOKN" }, NAMES),
    ).toEqual([
      "No workspace secret named ACME_AGENT_TOKN. Pick one from the list.",
    ]);
    expect(
      authIssues({ kind: "bearer", secret: "ACME_AGENT_TOKEN" }, NAMES),
    ).toEqual([]);
    expect(
      authIssues({ kind: "oauth2", secret: "FI_PROJECT_KEY" }, NAMES),
    ).toHaveLength(2);
    expect(
      authIssues({ kind: "api_key", secret: "FI_PROJECT_KEY" }, NAMES),
    ).toHaveLength(1);
    expect(authIssues({ kind: "none" }, NAMES)).toEqual([]);
  });

  it("summarises and lists fields per kind", () => {
    expect(authSummary({ kind: "api_key", header: "X-Key", secret: "S" })).toBe(
      "API key header in X-Key · secret S",
    );
    expect(
      authSecrets({
        kind: "oauth2",
        secret: "S",
        token_url: "https://a",
        client_id: "c",
      }),
    ).toEqual(["S"]);
    expect(authFieldsOf("oauth2")).toEqual([
      "token_url",
      "client_id",
      "secret",
    ]);
    expect(
      cardAuthKind({
        securitySchemes: { b: { type: "http", scheme: "bearer" } },
      }),
    ).toBe("bearer");
    expect(cardAuthKind({})).toBeNull();
  });
});
