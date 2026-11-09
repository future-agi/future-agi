import { describe, expect, it } from "vitest";

import catalog from "../__fixtures__/catalog-none-added.json";
import {
  addRequestBody,
  credentialRequestBody,
  keyProblem,
  needsKey,
  urlVariableProblems,
} from "../connect-request";

// The provider cards below are the catalog API's own (CN9's views, dumped by
// the lane's fixture generator), so these bodies are the ones the vault and
// the add endpoint were shown to accept.
const provider = (appId, providerId) =>
  catalog.results
    .find((app) => app.id === appId)
    .providers.find((card) => card.id === providerId);

const KEY = "test-cn21-key-not-real";

describe("addRequestBody", () => {
  it("names the provider and sends only the URL variables the user typed", () => {
    expect(
      addRequestBody(provider("bolt-crm", "bolt-mcp"), {
        accountHost: "acme.bolt.example.com",
        workspace: "sales",
        team: "",
      }),
    ).toEqual({
      provider: "bolt-mcp",
      url_variables: {
        accountHost: "acme.bolt.example.com",
        workspace: "sales",
      },
    });
  });

  it("drops a value for a variable the provider does not declare", () => {
    expect(
      addRequestBody(provider("acme-docs", "acme-mcp"), { accountHost: "x" }),
    ).toEqual({ provider: "acme-mcp", url_variables: {} });
  });

  it("carries the typed key as credential.value for a provider that takes one", () => {
    // CN-ADD: the add lists the provider's tools with this key and stores it
    // in the vault in the same write; where it goes comes from the catalog.
    expect(addRequestBody(provider("acme-docs", "acme-mcp"), {}, KEY)).toEqual({
      provider: "acme-mcp",
      url_variables: {},
      credential: { value: KEY },
    });
  });

  it("sends the key exactly as typed, never trimmed or changed", () => {
    const typed = `${KEY}-0001`;
    const body = addRequestBody(provider("acme-docs", "acme-mcp"), {}, typed);
    expect(body.credential.value).toBe(typed);
  });

  it("sends no credential for a provider that needs none", () => {
    // The add refuses a credential for such a provider
    // (connector_credential_not_taken).
    expect(
      addRequestBody(provider("cobalt-search", "cobalt-mcp"), {}, KEY),
    ).toEqual({ provider: "cobalt-mcp", url_variables: {} });
  });

  it("sends no credential when no key was typed", () => {
    expect(addRequestBody(provider("acme-docs", "acme-mcp"), {})).toEqual({
      provider: "acme-mcp",
      url_variables: {},
    });
  });
});

describe("urlVariableProblems", () => {
  const bolt = provider("bolt-crm", "bolt-mcp");

  it("asks for a variable with no default", () => {
    expect(urlVariableProblems(bolt, { workspace: "sales" })).toEqual({
      accountHost: "Required.",
    });
  });

  it("lets a variable with a default stay empty", () => {
    expect(
      urlVariableProblems(bolt, {
        accountHost: "acme.bolt.example.com",
        workspace: "sales",
      }),
    ).toEqual({});
  });

  it("checks a value against the catalog's pattern", () => {
    expect(
      urlVariableProblems(bolt, {
        accountHost: "evil.example.com",
        workspace: "sales",
      }),
    ).toEqual({ accountHost: expect.stringContaining("Does not match") });
  });

  it("refuses a value with a space, which the server refuses", () => {
    expect(
      urlVariableProblems(bolt, {
        accountHost: "acme.bolt.example.com",
        workspace: "sales team",
      }),
    ).toEqual({ workspace: expect.stringContaining("space") });
  });
});

describe("credentialRequestBody", () => {
  it("sends an API key header by its header name", () => {
    const card = provider("acme-docs", "acme-mcp");
    expect(credentialRequestBody("acme-docs", card.credential, KEY)).toEqual({
      server_id: "acme-docs",
      kind: "api_key",
      header_name: "X-Acme-Key",
      value: KEY,
      scope: "shared",
    });
  });

  it("sends a bearer token with no header name (the vault puts it in Authorization)", () => {
    const card = provider("bolt-crm", "bolt-mcp");
    expect(credentialRequestBody("bolt-crm", card.credential, KEY)).toEqual({
      server_id: "bolt-crm",
      kind: "bearer",
      value: KEY,
      scope: "shared",
    });
  });

  it("sends a query-parameter key by where it goes and its name", () => {
    const card = provider("fern-files", "fern-mcp");
    expect(credentialRequestBody("fern-files", card.credential, KEY)).toEqual({
      server_id: "fern-files",
      kind: "api_key",
      in: "query",
      name: "api_key",
      value: KEY,
      scope: "shared",
    });
  });

  it("sends a URL-variable key by where it goes and its name", () => {
    const card = provider("fern-files", "fern-path");
    expect(credentialRequestBody("fern-files", card.credential, KEY)).toEqual({
      server_id: "fern-files",
      kind: "api_key",
      in: "url_variable",
      name: "apiKey",
      value: KEY,
      scope: "shared",
    });
  });

  it("never sends a constant part: the gateway adds it from the catalog", () => {
    const card = provider("echo-chat", "echo-mcp");
    const body = credentialRequestBody("echo-chat", card.credential, KEY);
    expect(body).toEqual({
      server_id: "echo-chat",
      kind: "api_key",
      header_name: "Echo-Key",
      value: KEY,
      scope: "shared",
    });
    expect(JSON.stringify(body)).not.toContain("Echo-Scope");
  });
});

describe("needsKey and keyProblem", () => {
  it("knows which providers take a key", () => {
    expect(needsKey(provider("acme-docs", "acme-mcp").credential)).toBe(true);
    expect(needsKey(provider("cobalt-search", "cobalt-mcp").credential)).toBe(
      false,
    );
  });

  it("refuses an empty key or one with spaces around it, never altering it", () => {
    expect(keyProblem("")).toBe("Enter the key.");
    expect(keyProblem(` ${KEY}`)).toContain("spaces");
    expect(keyProblem(`${KEY}\n`)).toContain("spaces");
    expect(keyProblem(KEY)).toBeNull();
  });

  it("refuses a control character inside the key, as the vault does", () => {
    const inside = `${KEY.slice(0, 4)}\u0007${KEY.slice(4)}`;
    expect(keyProblem(inside)).toBe(
      "Remove the line break or other control character inside the key.",
    );
    expect(keyProblem(`${KEY.slice(0, 4)}\u007f${KEY.slice(4)}`)).toBe(
      "Remove the line break or other control character inside the key.",
    );
    expect(keyProblem(`${KEY.slice(0, 4)}é${KEY.slice(4)}`)).toBeNull();
  });
});
