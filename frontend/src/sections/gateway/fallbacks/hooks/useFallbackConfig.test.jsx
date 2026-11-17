import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useFallbackConfig } from "./useFallbackConfig";

const mockMutateAsync = vi.fn();
let mockActiveConfig;

vi.mock("../../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({
    data: mockActiveConfig,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gateway-1", isLoading: false }),
}));

// The real buildConfigPatch; only the request is stubbed.
vi.mock("../../hooks/useConfigPatch", async (importOriginal) => ({
  ...(await importOriginal()),
  useConfigPatch: () => ({ mutateAsync: mockMutateAsync, isPending: false }),
}));

// A save that created a whole new org-config version reset every section its
// payload left out. Fallback saves go through update-config instead, which
// deep-merges only the sections a patch sends, and send only the routing keys
// the Fallbacks page edits.
describe("useFallbackConfig saveRouting", () => {
  beforeEach(() => {
    mockMutateAsync.mockClear();
  });

  it("keeps MCP, A2A, audit and model sections when saving routing", () => {
    mockActiveConfig = {
      guardrails: { rules: [{ name: "pii-detector" }] },
      routing: { strategy: "weighted", fallback_enabled: false },
      rateLimiting: { enabled: true, rpm: 60 },
      budgets: { enabled: true },
      mcp: { servers: [{ name: "docs" }] },
      a2a: { agents: [{ name: "planner" }] },
      audit: { enabled: true },
      modelDatabase: { overrides: { "custom-model": { input: 1 } } },
      model_map: { fast: "gpt-4o-mini" },
    };
    const { result } = renderHook(() => useFallbackConfig());

    result.current.saveRouting({
      strategy: "least-latency",
      fallback_enabled: true,
    });

    expect(mockMutateAsync).toHaveBeenCalledTimes(1);
    const [{ gatewayId, patch }] = mockMutateAsync.mock.calls[0];
    expect(gatewayId).toBe("gateway-1");
    // Only routing is sent, so every other section stays as stored.
    expect(Object.keys(patch)).toEqual(["routing"]);
    // And only the fallback keys: the strategy belongs to another page.
    expect(patch.routing).toEqual({ fallback_enabled: true });
  });
});
