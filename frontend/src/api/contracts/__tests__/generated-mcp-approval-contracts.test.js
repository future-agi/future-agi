import { describe, expect, it } from "vitest";

import { API_SURFACE_PATHS } from "../api-surface.generated";

describe("generated MCP approval surface", () => {
  it.each([
    ["/agentcc/mcp/approvals/", "get"],
    ["/agentcc/mcp/approvals/{approval_id}/", "get"],
    ["/agentcc/mcp/approvals/{approval_id}/decision/", "post"],
  ])("publishes %s with %s", (apiPath, method) => {
    expect(API_SURFACE_PATHS[apiPath]).toContain(method);
  });
});
