import { describe, expect, it, vi } from "vitest";
import {
  agentccMcpConnectDelete,
  getAgentccMcpConnectDeleteUrl,
} from "src/generated/api-contracts/api";
import { apiMutator } from "src/api/contracts/openapi-mutator";

vi.mock("src/api/contracts/openapi-mutator", () => ({
  apiMutator: vi.fn(async () => ({ status: 200 })),
}));

describe("generated MCP disconnect contract", () => {
  it("keeps user mode as the default and can explicitly select shared", () => {
    expect(getAgentccMcpConnectDeleteUrl("issues")).toBe(
      "/agentcc/mcp/connect/issues/",
    );
    expect(getAgentccMcpConnectDeleteUrl("issues", { mode: "user" })).toBe(
      "/agentcc/mcp/connect/issues/?mode=user",
    );
    expect(getAgentccMcpConnectDeleteUrl("issues", { mode: "shared" })).toBe(
      "/agentcc/mcp/connect/issues/?mode=shared",
    );
  });

  it("sends the selector through the generated client with request options intact", async () => {
    await agentccMcpConnectDelete(
      "issues",
      {},
      { mode: "shared" },
      { credentials: "include" },
    );
    expect(apiMutator).toHaveBeenLastCalledWith(
      "/agentcc/mcp/connect/issues/?mode=shared",
      expect.objectContaining({
        method: "DELETE",
        credentials: "include",
        body: "{}",
      }),
    );
  });
});
