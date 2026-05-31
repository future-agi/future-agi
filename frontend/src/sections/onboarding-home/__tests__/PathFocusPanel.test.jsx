import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, within } from "src/utils/test-utils";
import { getActivationStateFixture } from "../fixtures/activation-state.fixtures";
import { normalizeActivationState } from "../activation-state-utils";
import { dismissDestinationTourAnchor } from "src/sections/onboarding-tour/destinationTourDismissal";
import PathFocusPanel from "../components/PathFocusPanel";

const renderPanel = (fixtureName, overrides = {}) => {
  const state = normalizeActivationState(
    getActivationStateFixture(fixtureName),
  );
  return {
    state,
    ...render(
      <PathFocusPanel
        action={state.recommendedAction}
        fallbackAction={state.fallbackAction}
        primaryPath={state.primaryPath}
        stage={state.stage}
        {...overrides}
      />,
    ),
  };
};

describe("PathFocusPanel", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    window.sessionStorage.setItem("currentUserId", "usr-home");
  });

  it("guides the prompt path through testing and versioning", async () => {
    const onPrimaryClick = vi.fn();
    renderPanel("promptCreatedNoRun", { onPrimaryClick });

    const panel = screen.getByTestId("path-focus-panel-prompt");
    expect(panel).toBeVisible();
    expect(
      within(panel).getByText("Build a prompt quality loop"),
    ).toBeVisible();
    expect(
      within(panel).getByText(
        "Create one prompt, test it, save a baseline, and compare the next version.",
      ),
    ).toBeVisible();
    expect(
      within(screen.getByTestId("path-focus-step-start_prompt")).getByText(
        "Done",
      ),
    ).toBeVisible();
    expect(
      within(screen.getByTestId("path-focus-step-run_prompt_test")).getByText(
        "Now",
      ),
    ).toBeVisible();
    expect(
      within(
        screen.getByTestId("path-focus-step-save_prompt_version"),
      ).getByText("Next"),
    ).toBeVisible();
    expect(within(panel).getByTestId("current-step-guide")).toHaveTextContent(
      "Run one focused example before saving.",
    );

    await userEvent.click(
      within(panel).getByRole("link", { name: "Run test" }),
    );

    expect(onPrimaryClick).toHaveBeenCalledWith(
      expect.objectContaining({ id: "run_prompt_test" }),
    );
  });

  it("uses a backend journey plan when one is provided", () => {
    const { state } = renderPanel("promptCreatedNoRun", {
      journeyPlan: {
        id: "prompt_first_run",
        primaryPath: "prompt",
        eyebrow: "Prompt loop",
        title: "Test from manifest",
        description: "Manifest copy wins over bundled fallback copy.",
        chips: ["prompt"],
        currentStepId: "run_prompt_test",
        currentStepIndex: 1,
        steps: [
          {
            id: "create_prompt",
            stage: "start_prompt",
            label: "Create prompt",
            description: "Create from manifest.",
            status: "complete",
          },
          {
            id: "run_prompt_test",
            stage: "run_prompt_test",
            label: "Run manifest test",
            description: "Run from manifest.",
            status: "current",
            tourAnchor: "prompt_run_test_button",
          },
        ],
      },
    });

    const panel = screen.getByTestId(`path-focus-panel-${state.primaryPath}`);
    expect(within(panel).getByText("Test from manifest")).toBeVisible();
    expect(
      within(screen.getByTestId("path-focus-step-run_prompt_test")).getByText(
        "Run manifest test",
      ),
    ).toBeVisible();
    expect(
      within(screen.getByTestId("path-focus-step-run_prompt_test")).getByText(
        "Now",
      ),
    ).toBeVisible();
    expect(within(panel).getByTestId("current-step-guide")).toHaveTextContent(
      "Run from manifest.",
    );
    expect(
      within(panel).getByRole("link", { name: "Run test" }),
    ).toHaveAttribute(
      "href",
      "/dashboard/workbench/create/prompt-1?source=onboarding&onboarding=run-test&tour_anchor=prompt_run_test_button&journey_step=run_prompt_test",
    );
    expect(within(panel).queryByRole("link", { name: /show tip/i })).toBeNull();
  });

  it("keeps first-run focus on the current action before showing the full path", async () => {
    const onPrimaryClick = vi.fn();
    renderPanel("promptCreatedNoRun", {
      onPrimaryClick,
      singleActionFocus: true,
    });

    const panel = screen.getByTestId("path-focus-panel-prompt");
    expect(within(panel).getByText("Step 2 of 6")).toBeVisible();
    expect(within(panel).getByTestId("current-step-guide")).toHaveTextContent(
      "Run one focused example before saving.",
    );
    expect(
      screen.queryByTestId("path-focus-step-start_prompt"),
    ).not.toBeInTheDocument();
    expect(
      within(panel).queryByRole("link", { name: /open workbench/i }),
    ).not.toBeInTheDocument();

    await userEvent.click(
      within(panel).getByRole("link", { name: "Run test" }),
    );

    expect(onPrimaryClick).toHaveBeenCalledWith(
      expect.objectContaining({ id: "run_prompt_test" }),
    );

    await userEvent.click(
      within(panel).getByRole("button", { name: /show full path/i }),
    );

    expect(
      screen.getByTestId("path-focus-step-start_prompt"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("path-focus-step-run_prompt_test")).getByText(
        "Now",
      ),
    ).toBeVisible();
  });

  it("offers a replay link after the current destination tip was dismissed", () => {
    dismissDestinationTourAnchor({
      anchor: "prompt_run_test_button",
      identity: "usr-home",
    });

    const { state } = renderPanel("promptCreatedNoRun", {
      journeyPlan: {
        id: "prompt_first_run",
        primaryPath: "prompt",
        eyebrow: "Prompt loop",
        title: "Test from manifest",
        description: "Manifest copy wins over bundled fallback copy.",
        chips: ["prompt"],
        currentStepId: "run_prompt_test",
        currentStepIndex: 1,
        steps: [
          {
            id: "create_prompt",
            stage: "start_prompt",
            label: "Create prompt",
            description: "Create from manifest.",
            status: "complete",
          },
          {
            id: "run_prompt_test",
            stage: "run_prompt_test",
            label: "Run manifest test",
            description: "Run from manifest.",
            status: "current",
            tourAnchor: "prompt_run_test_button",
          },
        ],
      },
    });

    const panel = screen.getByTestId(`path-focus-panel-${state.primaryPath}`);
    expect(
      within(panel).getByRole("link", { name: /show tip/i }),
    ).toHaveAttribute(
      "href",
      "/dashboard/workbench/create/prompt-1?source=onboarding&onboarding=run-test&tour_anchor=prompt_run_test_button&journey_step=run_prompt_test&tour_replay=1",
    );
  });

  it("guides the gateway path from key setup to first routed request", () => {
    renderPanel("gatewayKeyNoRequest");

    const panel = screen.getByTestId("path-focus-panel-gateway");
    expect(panel).toBeVisible();
    expect(within(panel).getByText("Route one request safely")).toBeVisible();
    expect(
      within(
        screen.getByTestId("path-focus-step-configure_gateway_provider"),
      ).getByText("Done"),
    ).toBeVisible();
    expect(
      within(
        screen.getByTestId("path-focus-step-create_gateway_key"),
      ).getByText("Done"),
    ).toBeVisible();
    expect(
      within(
        screen.getByTestId("path-focus-step-run_gateway_request"),
      ).getByText("Now"),
    ).toBeVisible();
    expect(
      within(panel).getByRole("link", { name: "Send request" }),
    ).toHaveAttribute(
      "href",
      "/dashboard/gateway?onboarding=test-request&tour_anchor=gateway_request_button&journey_step=run_gateway_request",
    );
  });

  it("does not add step anchors when the selected path stage is unavailable", () => {
    renderPanel("selectedPathUnavailable");

    const panel = screen.getByTestId("path-focus-panel-prompt");
    expect(panel).toBeVisible();
    expect(within(panel).queryByTestId("current-step-guide")).toBeNull();
    within(panel)
      .getAllByRole("link", { name: "Start with observe" })
      .forEach((link) => {
        expect(link).toHaveAttribute(
          "href",
          "/dashboard/observe?setup=true&source=onboarding",
        );
      });
  });

  it("renders nothing for paths without a focused plan", () => {
    const { container } = render(
      <PathFocusPanel primaryPath="sample" stage="review_sample_signal" />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});
