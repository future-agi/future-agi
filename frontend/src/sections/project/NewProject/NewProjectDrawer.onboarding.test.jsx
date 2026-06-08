import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { renderWithRouter, screen } from "src/utils/test-utils";

import NewProjectDrawer from "./NewProjectDrawer";

vi.mock("./NewObserve", () => ({
  default: ({ setupVerification, showFirstTraceGuide }) => (
    <div data-testid="new-observe" data-show-guide={showFirstTraceGuide}>
      {setupVerification ? (
        <div>
          <div>{setupVerification.title}</div>
          <div>{setupVerification.description}</div>
          {setupVerification.primaryAction ? (
            <button
              type="button"
              onClick={setupVerification.primaryAction.onClick}
            >
              {setupVerification.primaryAction.label}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  ),
}));

vi.mock("./NewExperiment", () => ({
  default: () => <div>Prototype setup</div>,
}));

describe("NewProjectDrawer onboarding", () => {
  it("passes observe trace wait state into package setup", async () => {
    const user = userEvent.setup();
    const onWait = vi.fn();

    renderWithRouter(
      <NewProjectDrawer
        open
        onClose={vi.fn()}
        observeSetupVerification={{
          description:
            "Run one Anthropic TypeScript request after pasting the setup.",
          primaryAction: {
            label: "Check for Anthropic TypeScript trace",
            onClick: onWait,
          },
          status: "waiting",
          title: "Waiting for Anthropic TypeScript trace",
        }}
      />,
      {
        route:
          "/dashboard/observe?setup=true&source=onboarding&provider=anthropic&language=typescript",
      },
    );

    expect(screen.getByTestId("new-observe")).toHaveAttribute(
      "data-show-guide",
      "true",
    );
    expect(
      screen.getByText("Waiting for Anthropic TypeScript trace"),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Run one Anthropic TypeScript request after pasting the setup.",
      ),
    ).toBeVisible();

    await user.click(
      screen.getByRole("button", {
        name: /check for anthropic typescript trace/i,
      }),
    );

    expect(onWait).toHaveBeenCalledTimes(1);
  });
});
