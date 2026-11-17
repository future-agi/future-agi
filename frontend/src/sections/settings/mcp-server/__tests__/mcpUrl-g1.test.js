// L7c g1 (the capability contract b): the MCP address is this environment's, never a
// fixed production host.
import { describe, expect, it } from "vitest";
import { mcpUrlOf } from "../mcpUrl";

describe("mcpUrlOf", () => {
  it("prefers the server's own mcp_url", () => {
    expect(
      mcpUrlOf("https://mcp.example.test/mcp", "http://127.0.0.1:8999"),
    ).toBe("https://mcp.example.test/mcp");
  });
  it("derives the address from the app's configured API base", () => {
    expect(mcpUrlOf(null, "http://127.0.0.1:8999/")).toBe(
      "http://127.0.0.1:8999/mcp",
    );
    expect(mcpUrlOf("", "https://api.staging.example.test")).toBe(
      "https://api.staging.example.test/mcp",
    );
  });
  it("is null (no guide) when nothing is configured; never a production host", () => {
    expect(mcpUrlOf(undefined, "")).toBeNull();
    expect(mcpUrlOf(undefined, undefined)).toBeNull();
    expect(mcpUrlOf(undefined, "/relative")).toBeNull();
  });
});
