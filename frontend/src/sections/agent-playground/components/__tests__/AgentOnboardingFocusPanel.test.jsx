import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
import AgentOnboardingFocusPanel from "../AgentOnboardingFocusPanel";

describe("AgentOnboardingFocusPanel", () => {
  it("does not render when hidden", () => {
    render(
      <AgentOnboardingFocusPanel
        hidden
        description="Hidden description"
        title="Hidden title"
      />,
    );

    expect(screen.queryByTestId("agent-onboarding-focus")).toBeNull();
  });

  it("renders agent setup steps and actions", async () => {
    const onPrimary = vi.fn();
    const onSecondary = vi.fn();

    render(
      <AgentOnboardingFocusPanel
        currentStep="Scenario"
        description="Run one agent scenario and inspect the output."
        primaryAction={{ label: "Run workflow", onClick: onPrimary }}
        secondaryAction={{ label: "Open executions", onClick: onSecondary }}
        steps={[
          { label: "Agent", complete: true },
          { label: "Scenario", complete: false },
          { label: "Review", complete: false },
        ]}
        title="Run the first agent workflow"
        tourAnchor="agent_run_scenario_button"
      />,
    );

    expect(screen.getByText("Agent setup")).toBeVisible();
    expect(screen.getByText("Step 2 of 3")).toBeVisible();
    expect(screen.getByText("Run the first agent workflow")).toBeVisible();
    expect(screen.getByText("Agent")).toBeVisible();
    expect(screen.getAllByText("Scenario").length).toBeGreaterThan(0);
    expect(screen.getByText("Review")).toBeVisible();
    expect(
      screen.getByRole("button", { name: /run workflow/i }),
    ).toHaveAttribute("data-tour-anchor", "agent_run_scenario_button");

    await userEvent.click(
      screen.getByRole("button", { name: /open executions/i }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /run workflow/i }),
    );

    expect(onSecondary).toHaveBeenCalledTimes(1);
    expect(onPrimary).toHaveBeenCalledTimes(1);
  });

  it("shows a blocker chip when provided", () => {
    render(
      <AgentOnboardingFocusPanel
        blocker="Add one node first"
        currentStep="Scenario"
        description="The agent needs a runnable node before it can execute."
        title="Run the first agent workflow"
      />,
    );

    expect(screen.getByText("Add one node first")).toBeVisible();
  });

  it("hides secondary actions in single-action focus mode", () => {
    render(
      <AgentOnboardingFocusPanel
        singleActionFocus
        currentStep="Agent"
        description="Create one agent and run it once."
        primaryAction={{ label: "Create Agent", onClick: vi.fn() }}
        secondaryAction={{ label: "Open first agent", onClick: vi.fn() }}
        title="Create the first agent"
      />,
    );

    expect(screen.getByRole("button", { name: /create agent/i })).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /open first agent/i }),
    ).not.toBeInTheDocument();
  });

  it("uses the first incomplete step when current step copy is omitted", () => {
    render(
      <AgentOnboardingFocusPanel
        description="Add an eval node for the reviewed behavior."
        steps={[
          { label: "Agent", complete: true },
          { label: "Scenario", complete: true },
          { label: "Review", complete: true },
          { label: "Coverage", complete: false },
        ]}
        title="Add coverage"
      />,
    );

    expect(screen.getByText("Step 4 of 4")).toBeVisible();
  });
});
