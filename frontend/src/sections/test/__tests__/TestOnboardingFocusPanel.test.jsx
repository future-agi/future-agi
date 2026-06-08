import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
import TestOnboardingFocusPanel from "../TestOnboardingFocusPanel";

describe("TestOnboardingFocusPanel", () => {
  it("does not render when hidden", () => {
    render(
      <TestOnboardingFocusPanel
        hidden
        description="Hidden description"
        title="Hidden title"
      />,
    );

    expect(screen.queryByTestId("test-onboarding-focus")).toBeNull();
  });

  it("renders eval setup steps and actions", async () => {
    const onPrimary = vi.fn();
    const onSecondary = vi.fn();

    render(
      <TestOnboardingFocusPanel
        currentStep="Evaluation"
        description="Add an evaluation and run it against selected test rows."
        primaryAction={{ label: "Add Evaluation", onClick: onPrimary }}
        secondaryAction={{ label: "Run Evaluation", onClick: onSecondary }}
        steps={[
          { label: "Test", complete: true },
          { label: "Evaluation", complete: false },
          { label: "Run", complete: false },
        ]}
        title="Add evaluation coverage"
        tourAnchor="voice_success_criteria_button"
      />,
    );

    expect(screen.getByText("Eval setup")).toBeVisible();
    expect(screen.getByText("Step 2 of 3")).toBeVisible();
    expect(screen.getByText("Add evaluation coverage")).toBeVisible();
    expect(screen.getByText("Test")).toBeVisible();
    expect(screen.getAllByText("Evaluation").length).toBeGreaterThan(0);
    expect(screen.getByText("Run")).toBeVisible();
    expect(
      screen.getByRole("button", { name: /add evaluation/i }),
    ).toHaveAttribute("data-tour-anchor", "voice_success_criteria_button");

    await userEvent.click(
      screen.getByRole("button", { name: /run evaluation/i }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /add evaluation/i }),
    );

    expect(onSecondary).toHaveBeenCalledTimes(1);
    expect(onPrimary).toHaveBeenCalledTimes(1);
  });

  it("shows a blocker chip when provided", () => {
    render(
      <TestOnboardingFocusPanel
        blocker="Select a run first"
        currentStep="Run"
        description="Choose at least one row before running the evaluation."
        title="Run the first evaluation"
      />,
    );

    expect(screen.getByText("Select a run first")).toBeVisible();
  });

  it("hides secondary actions in single-action focus mode", () => {
    render(
      <TestOnboardingFocusPanel
        singleActionFocus
        currentStep="Evaluation"
        description="Add one evaluation before running it."
        primaryAction={{ label: "Add Evaluation", onClick: vi.fn() }}
        secondaryAction={{ label: "Run Evaluation", onClick: vi.fn() }}
        title="Create eval coverage"
      />,
    );

    expect(
      screen.getByRole("button", { name: /add evaluation/i }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /run evaluation/i }),
    ).not.toBeInTheDocument();
  });

  it("supports voice setup labeling for shared guided panels", () => {
    render(
      <TestOnboardingFocusPanel
        currentStep="Test call"
        description="Run one voice test call."
        eyebrow="Voice setup"
        primaryAction={{ label: "Run test call", onClick: vi.fn() }}
        title="Run a voice test call"
      />,
    );

    expect(screen.getByText("Voice setup")).toBeVisible();
    expect(screen.queryByText("Eval setup")).not.toBeInTheDocument();
  });

  it("uses the first incomplete step when current step copy is omitted", () => {
    render(
      <TestOnboardingFocusPanel
        description="Run the saved evaluation."
        steps={[
          { label: "Test", complete: true },
          { label: "Evaluation", complete: true },
          { label: "Run", complete: false },
        ]}
        title="Run evaluation"
      />,
    );

    expect(screen.getByText("Step 3 of 3")).toBeVisible();
  });
});
