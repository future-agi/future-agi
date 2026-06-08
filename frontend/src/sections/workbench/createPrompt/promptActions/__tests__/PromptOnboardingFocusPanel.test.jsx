import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
import PromptOnboardingFocusPanel from "../PromptOnboardingFocusPanel";

describe("PromptOnboardingFocusPanel", () => {
  it("does not render outside onboarding route context", () => {
    render(<PromptOnboardingFocusPanel currentTab="Playground" />);

    expect(screen.queryByTestId("prompt-onboarding-focus")).toBeNull();
  });

  it("runs the prompt when the run-test mode is already on Playground", async () => {
    const onRunPrompt = vi.fn();

    render(
      <PromptOnboardingFocusPanel
        currentTab="Playground"
        mode="run-test"
        onRunPrompt={onRunPrompt}
        tourAnchor="prompt_run_button"
      />,
    );

    expect(screen.getByText("Run one prompt test")).toBeVisible();
    expect(screen.getByText("Prompt setup")).toBeVisible();
    expect(screen.getByText("Step 2 of 6")).toBeVisible();
    expect(screen.getByRole("button", { name: /run prompt/i })).toHaveAttribute(
      "data-tour-anchor",
      "prompt_run_button",
    );

    await userEvent.click(screen.getByRole("button", { name: /run prompt/i }));

    expect(onRunPrompt).toHaveBeenCalledTimes(1);
  });

  it("moves the user back to Playground before running a prompt test", async () => {
    const onOpenPlayground = vi.fn();

    render(
      <PromptOnboardingFocusPanel
        currentTab="Metrics"
        mode="run-test"
        onOpenPlayground={onOpenPlayground}
      />,
    );

    expect(screen.getByText("Run one prompt test")).toBeVisible();

    await userEvent.click(
      screen.getByRole("button", { name: /open playground/i }),
    );

    expect(onOpenPlayground).toHaveBeenCalledTimes(1);
  });

  it("opens the save-version action from the save-version mode", async () => {
    const onOpenSaveVersion = vi.fn();

    render(
      <PromptOnboardingFocusPanel
        currentTab="Playground"
        mode="save-version"
        onOpenSaveVersion={onOpenSaveVersion}
      />,
    );

    expect(screen.getByText("Save the prompt baseline")).toBeVisible();

    await userEvent.click(
      screen.getByRole("button", { name: /save version/i }),
    );

    expect(onOpenSaveVersion).toHaveBeenCalledTimes(1);
  });

  it("guides compare mode to create a second version before version history", async () => {
    const onCreateSecondVersion = vi.fn();

    render(
      <PromptOnboardingFocusPanel
        compareNeedsSecondVersion
        currentTab="Playground"
        mode="compare"
        onCreateSecondVersion={onCreateSecondVersion}
      />,
    );

    expect(screen.getByText("Create a second version")).toBeVisible();
    expect(screen.getByText("Step 4 of 6")).toBeVisible();

    await userEvent.click(
      screen.getByRole("button", { name: /create second version/i }),
    );

    expect(onCreateSecondVersion).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByRole("button", { name: /open version history/i }),
    ).toBeNull();
  });

  it("keeps second-version guidance while the user runs and saves the edit", async () => {
    const onRunPrompt = vi.fn();
    const onOpenSaveVersion = vi.fn();

    const { rerender } = render(
      <PromptOnboardingFocusPanel
        currentTab="Playground"
        journeyStep="create_second_prompt_version"
        mode="run-test"
        onRunPrompt={onRunPrompt}
      />,
    );

    expect(screen.getByText("Run the second version")).toBeVisible();
    expect(screen.getByText("Step 4 of 6")).toBeVisible();

    await userEvent.click(
      screen.getByRole("button", { name: /run second version/i }),
    );

    expect(onRunPrompt).toHaveBeenCalledTimes(1);

    rerender(
      <PromptOnboardingFocusPanel
        currentTab="Playground"
        journeyStep="create_second_prompt_version"
        mode="save-version"
        onOpenSaveVersion={onOpenSaveVersion}
      />,
    );

    expect(screen.getByText("Save the second version")).toBeVisible();

    await userEvent.click(
      screen.getByRole("button", { name: /save second version/i }),
    );

    expect(onOpenSaveVersion).toHaveBeenCalledTimes(1);
  });

  it("blocks save-version guidance when no draft target is available", () => {
    render(
      <PromptOnboardingFocusPanel
        currentTab="Playground"
        isSaveDisabled
        mode="save-version"
      />,
    );

    expect(screen.getByText("Save the prompt baseline")).toBeVisible();
    expect(
      screen.getByRole("button", { name: /save version/i }),
    ).toBeDisabled();
  });

  it("shows the onboarding source default prompt guidance", () => {
    render(
      <PromptOnboardingFocusPanel
        currentTab="Playground"
        source="onboarding"
      />,
    );

    expect(screen.getByText("Create the first prompt")).toBeVisible();
  });
});
