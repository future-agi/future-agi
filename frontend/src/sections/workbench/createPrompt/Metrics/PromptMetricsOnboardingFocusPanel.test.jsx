import React from "react";
import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
import { METRIC_TAB_IDS } from "./constants";
import PromptMetricsOnboardingFocusPanel from "./PromptMetricsOnboardingFocusPanel";

describe("PromptMetricsOnboardingFocusPanel", () => {
  it("does not render outside guided metrics onboarding", () => {
    render(
      <PromptMetricsOnboardingFocusPanel
        activeTab={METRIC_TAB_IDS.METRICS}
        isOnboarding={false}
      />,
    );

    expect(screen.queryByTestId("prompt-metrics-onboarding-focus")).toBeNull();
  });

  it("opens filters and linked traces from guided metrics onboarding", async () => {
    const onOpenFilters = vi.fn();
    const onOpenLinkedTraces = vi.fn();

    render(
      <PromptMetricsOnboardingFocusPanel
        activeTab={METRIC_TAB_IDS.METRICS}
        isOnboarding
        onOpenFilters={onOpenFilters}
        onOpenLinkedTraces={onOpenLinkedTraces}
      />,
    );

    expect(screen.getByText("Review the prompt quality signal")).toBeVisible();

    await userEvent.click(
      screen.getByRole("button", { name: /filter weak versions/i }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /linked traces/i }),
    );

    expect(onOpenFilters).toHaveBeenCalledTimes(1);
    expect(onOpenLinkedTraces).toHaveBeenCalledTimes(1);
  });

  it("disables the linked traces action when linked traces are already open", () => {
    render(
      <PromptMetricsOnboardingFocusPanel
        activeTab={METRIC_TAB_IDS.LINKED_TRACES}
        isOnboarding
      />,
    );

    expect(
      screen.getByRole("button", { name: /linked traces/i }),
    ).toBeDisabled();
  });
});
