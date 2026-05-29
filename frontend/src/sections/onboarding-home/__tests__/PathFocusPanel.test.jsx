import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, within } from "src/utils/test-utils";
import { getActivationStateFixture } from "../fixtures/activation-state.fixtures";
import { normalizeActivationState } from "../activation-state-utils";
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
    expect(within(panel).getByText("Run test: run prompt test")).toBeVisible();

    await userEvent.click(
      within(panel).getByRole("link", { name: "Run test" }),
    );

    expect(onPrimaryClick).toHaveBeenCalledWith(
      expect.objectContaining({ id: "run_prompt_test" }),
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
    ).toHaveAttribute("href", "/dashboard/gateway?onboarding=test-request");
  });

  it("renders nothing for paths without a focused plan", () => {
    const { container } = render(
      <PathFocusPanel primaryPath="sample" stage="review_sample_signal" />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});
