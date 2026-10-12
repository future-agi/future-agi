import { describe, it, expect, vi } from "vitest";
import React from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// TH-8084 (approved: Nikhil 2026-10-03 blanket, option 1): onboarding follows
// where the install runs. A licence ("ee") is still a self-hosted install.

const h = vi.hoisted(() => ({ mode: "oss" }));

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(async () => ({ data: { result: { mode: h.mode } } })),
  },
  endpoints: { settings: { v2: { deploymentInfo: "/api/deployment-info/" } } },
}));

import { useDeploymentMode } from "src/hooks/useDeploymentMode";

function renderMode(mode) {
  h.mode = mode;
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => useDeploymentMode(), { wrapper });
}

describe("useDeploymentMode isSelfHosted", () => {
  it.each([
    ["oss", true],
    ["ee", true],
    ["cloud", false],
  ])("%s -> isSelfHosted %s", async (mode, expected) => {
    const { result } = renderMode(mode);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.mode).toBe(mode);
    expect(result.current.isSelfHosted).toBe(expected);
  });
});
