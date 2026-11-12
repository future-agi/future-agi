import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

describe("Gateway consent browser entry", () => {
  it("registers the gateway authorization route separately from platform MCP", () => {
    const routes = source("../../routes/sections/index.jsx");
    expect(routes).toContain('path: "/gateway/mcp/authorize"');
    expect(routes).toContain("<GatewayOAuthConsent />");
    expect(routes).toContain('path: "/mcp/authorize"');
  });

  it("does not lose a gateway consent request to first-login onboarding", () => {
    const guard = source("../../auth/guard/auth-guard.jsx");
    expect(guard).toContain(
      'window.location.pathname === "/gateway/mcp/authorize"',
    );
  });

  it("exposes the MCP clients list in the existing console", () => {
    expect(source("../../sections/gateway/mcp/tabs.js")).toContain(
      'slug: "clients"',
    );
  });
});
