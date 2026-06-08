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

  it("opens filters, linked traces, and completion from guided metrics onboarding", async () => {
    const onCompleteLoop = vi.fn();
    const onOpenFilters = vi.fn();
    const onOpenLinkedTraces = vi.fn();

    render(
      <PromptMetricsOnboardingFocusPanel
        activeTab={METRIC_TAB_IDS.METRICS}
        isOnboarding
        onCompleteLoop={onCompleteLoop}
        onOpenFilters={onOpenFilters}
        onOpenLinkedTraces={onOpenLinkedTraces}
      />,
    );

    expect(screen.getByText("Review the prompt quality signal")).toBeVisible();
    expect(screen.getByText("Prompt setup")).toBeVisible();
    expect(screen.getByText("Step 6 of 6")).toBeVisible();

    await userEvent.click(
      screen.getByRole("button", { name: /filter weak versions/i }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /linked traces/i }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /finish setup/i }),
    );

    expect(onCompleteLoop).toHaveBeenCalledTimes(1);
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

  it("disables the completion action while completion is pending", () => {
    render(
      <PromptMetricsOnboardingFocusPanel
        activeTab={METRIC_TAB_IDS.METRICS}
        isCompletingLoop
        isOnboarding
      />,
    );

    expect(screen.getByRole("button", { name: /finishing/i })).toBeDisabled();
  });
});
