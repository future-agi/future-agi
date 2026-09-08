import { describe, expect, it } from "vitest";

import { credentialsFromHeaders } from "../src/auth.js";

describe("MCP authentication headers", () => {
  it("accepts an existing FutureAGI API key pair", () => {
    expect(
      credentialsFromHeaders(
        new Headers({ "X-Api-Key": "public", "X-Secret-Key": "secret" }),
      ),
    ).toEqual({ apiKey: "public", secretKey: "secret" });
  });

  it("accepts a bearer credential for direct API forwarding", () => {
    expect(
      credentialsFromHeaders(new Headers({ Authorization: "Bearer token" })),
    ).toEqual({ bearerToken: "token" });
  });

  it("rejects incomplete or mixed credentials", () => {
    expect(() =>
      credentialsFromHeaders(new Headers({ "X-Api-Key": "public" })),
    ).toThrow(/provided together/u);
    expect(() =>
      credentialsFromHeaders(
        new Headers({
          "X-Api-Key": "public",
          "X-Secret-Key": "secret",
          Authorization: "Bearer token",
        }),
      ),
    ).toThrow(/either/u);
  });
});
