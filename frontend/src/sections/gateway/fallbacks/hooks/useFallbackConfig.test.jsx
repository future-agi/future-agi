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
  useCreateOrgConfig: () => ({
    mutateAsync: mockMutateAsync,
    isPending: false,
  }),
}));

describe("useFallbackConfig saveRouting", () => {
  beforeEach(() => {
    mockMutateAsync.mockClear();
  });

  it("keeps MCP, A2A, audit and model sections when saving routing", () => {
    mockActiveConfig = {
      guardrails: { rules: [{ name: "pii-detector" }] },
      routing: { strategy: "weighted" },
      rateLimiting: { enabled: true, rpm: 60 },
      budgets: { enabled: true },
      mcp: { servers: [{ name: "docs" }] },
      a2a: { agents: [{ name: "planner" }] },
      audit: { enabled: true },
      modelDatabase: { "custom-model": { input: 1 } },
      model_map: { fast: "gpt-4o-mini" },
    };
    const { result } = renderHook(() => useFallbackConfig());

    result.current.saveRouting({ strategy: "least-latency" }, "Faster routing");

    expect(mockMutateAsync).toHaveBeenCalledWith({
      guardrails: { rules: [{ name: "pii-detector" }] },
      routing: { strategy: "least-latency" },
      rate_limiting: { enabled: true, rpm: 60 },
      budgets: { enabled: true },
      mcp: { servers: [{ name: "docs" }] },
      a2a: { agents: [{ name: "planner" }] },
      audit: { enabled: true },
      model_database: { "custom-model": { input: 1 } },
      model_map: { fast: "gpt-4o-mini" },
      change_description: "Faster routing",
    });
  });
});
