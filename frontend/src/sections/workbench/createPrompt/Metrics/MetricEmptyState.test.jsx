import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import MetricEmptyState from "./MetricEmptyState";

const renderEmptyState = (props = {}) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MetricEmptyState {...props} />
    </QueryClientProvider>,
  );
};

describe("MetricEmptyState", () => {
  it("shows queued evaluation copy during prompt metrics onboarding", () => {
    renderEmptyState({ isOnboarding: true });

    expect(screen.getByTestId("prompt-metrics-onboarding-empty")).toBeVisible();
    expect(screen.getByText("Evaluation run is queued")).toBeVisible();
    expect(screen.getByText("Both versions queued")).toBeVisible();
    expect(
      screen.queryByText(
        "Add prompt to begin monitoring performance indicators",
      ),
    ).toBeNull();
  });
});
