import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, within } from "src/utils/test-utils";
import { getActivationStateFixture } from "../fixtures/activation-state.fixtures";
import { normalizeActivationState } from "../activation-state-utils";
import ProductLoopStepper from "../components/ProductLoopStepper";

describe("ProductLoopStepper", () => {
  it("renders all product loop steps with normalized statuses", async () => {
    const onActionClick = vi.fn();
    const state = normalizeActivationState(
      getActivationStateFixture("observeNoSetup"),
    );

    render(
      <ProductLoopStepper
        fallbackAction={state.fallbackAction}
        goal={state.goal}
        onActionClick={onActionClick}
        primaryPath={state.primaryPath}
        progress={{
          build: "selected",
          observe: "complete",
        }}
        recommendedAction={state.recommendedAction}
        stage={state.stage}
      />,
    );

    expect(screen.getByText("Setup progress")).toBeVisible();
    expect(screen.getByText("1 of 5 complete")).toBeVisible();
    expect(
      within(screen.getByTestId("product-loop-step-build")).getByText("Build"),
    ).toBeVisible();
    expect(
      within(screen.getByTestId("product-loop-step-test")).getByText("Test"),
    ).toBeVisible();
    expect(
      within(screen.getByTestId("product-loop-step-observe")).getByText(
        "Observe",
      ),
    ).toBeVisible();
    expect(
      within(screen.getByTestId("product-loop-step-ship")).getByText("Ship"),
    ).toBeVisible();
    expect(
      within(screen.getByTestId("product-loop-step-improve")).getByText(
        "Improve",
      ),
    ).toBeVisible();
    expect(
      within(screen.getByTestId("product-loop-step-build")).getByText(
        "Current",
      ),
    ).toBeVisible();
    expect(
      within(screen.getByTestId("product-loop-step-observe")).getByText(
        "Complete",
      ),
    ).toBeVisible();
    expect(screen.getByText("Next step")).toBeVisible();
    expect(
      within(screen.getByTestId("product-loop-next-action")).getByText(
        "Connect your agent",
      ),
    ).toBeVisible();

    await userEvent.click(
      screen.getByRole("link", { name: /open next step/i }),
    );

    expect(onActionClick).toHaveBeenCalledWith(
      expect.objectContaining({ id: "create_observe_project" }),
    );
  });

  it("uses the fallback action when the recommended route is unavailable", () => {
    const state = normalizeActivationState(
      getActivationStateFixture("observeNoSetup"),
    );

    render(
      <ProductLoopStepper
        fallbackAction={state.fallbackAction}
        progress={{ build: "selected" }}
        recommendedAction={{
          ...state.recommendedAction,
          blocked: true,
          href: null,
          routeAvailable: false,
        }}
        stage="connect_observability"
      />,
    );

    expect(screen.getByText("Alternate setup")).toBeVisible();
    expect(screen.getByText("Open observe setup")).toBeVisible();
    expect(
      screen.getByRole("link", { name: /open alternate setup/i }),
    ).toHaveAttribute(
      "href",
      "/dashboard/observe?setup=true&source=onboarding",
    );
  });
});
