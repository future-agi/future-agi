import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { CAPABILITY } from "src/hooks/useCapabilities";
import { useFeatureRouteAnswer } from "../use-feature-route-answer";

// What the router reads to register a page that only some deployments run
// (dashboardRoutes' `falconAI`): "allowed" or "denied" once the deployment's
// capabilities answer, and "pending" before sign-in, while they load and
// when they could not be read. Capabilities are read only for a signed-in
// user.

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = /** @type {typeof import("src/utils/axios")} */ (
    await importOriginal()
  );
  return { ...actual, default: { ...actual.default, get } };
});

const answer = (allowed) => ({
  data: { features: { [CAPABILITY.FALCON_AI]: { allowed } } },
});

function renderAnswer(enabled) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(
    () => useFeatureRouteAnswer(CAPABILITY.FALCON_AI, { enabled }),
    { wrapper },
  );
}

beforeEach(() => {
  get.mockReset();
});

describe("useFeatureRouteAnswer", () => {
  it("is pending before sign-in, and reads no capabilities", () => {
    get.mockResolvedValue(answer(true));
    const { result } = renderAnswer(false);
    expect(result.current).toBe("pending");
    expect(get).not.toHaveBeenCalled();
  });

  it("is pending while the capabilities load, then allowed", async () => {
    get.mockResolvedValue(answer(true));
    const { result } = renderAnswer(true);
    expect(result.current).toBe("pending");
    await waitFor(() => expect(result.current).toBe("allowed"));
  });

  it("is denied when the deployment does not allow the feature", async () => {
    get.mockResolvedValue(answer(false));
    const { result } = renderAnswer(true);
    await waitFor(() => expect(result.current).toBe("denied"));
  });

  it("stays pending when the capabilities could not be read", async () => {
    get.mockRejectedValue(new Error("Network Error"));
    const { result } = renderAnswer(true);
    // useCapabilities retries a failed read once before it reports the error.
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2), {
      timeout: 4000,
    });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(result.current).toBe("pending");
  });
});
