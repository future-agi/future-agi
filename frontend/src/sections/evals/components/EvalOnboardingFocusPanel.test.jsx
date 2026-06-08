import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import EvalOnboardingFocusPanel from "./EvalOnboardingFocusPanel";

describe("EvalOnboardingFocusPanel", () => {
  it("does not render when hidden", () => {
    const onViewed = vi.fn();
    render(
      <EvalOnboardingFocusPanel
        hidden
        description="Hidden copy"
        onViewed={onViewed}
        title="Hidden title"
      />,
    );

    expect(screen.queryByTestId("eval-onboarding-focus")).toBeNull();
    expect(onViewed).not.toHaveBeenCalled();
  });

  it("renders the current eval onboarding step", () => {
    const onViewed = vi.fn();
    render(
      <EvalOnboardingFocusPanel
        currentStep="Scorer"
        description="Save one scorer so this source can be evaluated."
        onViewed={onViewed}
        primaryAction={{
          label: "Use trace project",
          onClick: vi.fn(),
        }}
        sourceSummary={{
          description: "The next scorer you save will evaluate this source.",
          label: "Dataset ready",
        }}
        steps={[
          { label: "Source", complete: true },
          { label: "Scorer", complete: false },
          { label: "Run", complete: false },
        ]}
        title="Add the eval scorer"
        tourAnchor="eval_scorer_button"
      />,
    );

    expect(screen.getByText("Eval setup")).toBeVisible();
    expect(screen.getByText("Step 2 of 3")).toBeVisible();
    expect(screen.getByText("Add the eval scorer")).toBeVisible();
    expect(
      screen.getByText("Save one scorer so this source can be evaluated."),
    ).toBeVisible();
    expect(screen.getByText("Dataset ready")).toBeVisible();
    expect(
      screen.getByText("The next scorer you save will evaluate this source."),
    ).toBeVisible();
    expect(screen.getByText("Source")).toBeVisible();
    expect(screen.getAllByText("Scorer").length).toBeGreaterThan(0);
    expect(screen.getByText("Run")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Use trace project" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Use trace project" }),
    ).toHaveAttribute("data-tour-anchor", "eval_scorer_button");
    expect(onViewed).toHaveBeenCalledTimes(1);
  });

  it("uses the first incomplete step when current step copy is omitted", () => {
    render(
      <EvalOnboardingFocusPanel
        description="Run the saved evaluator."
        steps={[
          { label: "Source", complete: true },
          { label: "Scorer", complete: true },
          { label: "Run", complete: false },
        ]}
        title="Run evaluator"
      />,
    );

    expect(screen.getByText("Step 3 of 3")).toBeVisible();
  });
});
