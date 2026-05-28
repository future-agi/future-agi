import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithRouter } from "src/utils/test-utils";
import ProjectFtux from "./ProjectFtux";

vi.mock("./NewProject/NewObserve", () => ({
  default: () => <div>Observe setup instructions</div>,
}));

vi.mock("./NewProject/NewExperiment", () => ({
  default: () => <div>Prototype setup instructions</div>,
}));

describe("ProjectFtux observe onboarding", () => {
  it("shows observe setup focus copy above the setup instructions", async () => {
    const user = userEvent.setup();
    const onPrimaryAction = vi.fn();

    renderWithRouter(
      <ProjectFtux
        observeSetupCopy={{
          currentStep: "Setup",
          description:
            "Install tracing, load your keys, and send one real or test request.",
          primaryLabel: "Review setup",
          steps: [
            { label: "Install", complete: false },
            { label: "Trace", complete: false },
            { label: "Review", complete: false },
          ],
          title: "Connect Observe to your app",
        }}
        observeSetupPrimaryAction={{
          label: "Review setup",
          onClick: onPrimaryAction,
        }}
        observeSetupSecondaryAction={{
          label: "Open sample trace",
          onClick: vi.fn(),
        }}
      />,
      { route: "/dashboard/observe?setup=true&source=onboarding" },
    );

    expect(screen.getByText("Connect Observe to your app")).toBeVisible();
    expect(screen.getByText("Install")).toBeVisible();
    expect(screen.getByText("Trace")).toBeVisible();
    expect(screen.getByText("Review")).toBeVisible();
    expect(screen.getByText("Observe setup instructions")).toBeVisible();
    expect(
      screen.getByRole("button", { name: /open sample trace/i }),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: /review setup/i }));

    expect(onPrimaryAction).toHaveBeenCalledTimes(1);
  });

  it("does not show observe setup focus copy on prototype FTUX", () => {
    renderWithRouter(
      <ProjectFtux
        observeSetupCopy={{
          currentStep: "Setup",
          description:
            "Install tracing, load your keys, and send one real or test request.",
          steps: [],
          title: "Connect Observe to your app",
        }}
      />,
      { route: "/dashboard/prototype?setup=true&source=onboarding" },
    );

    expect(screen.queryByText("Connect Observe to your app")).toBeNull();
    expect(screen.getByText("Prototype setup instructions")).toBeVisible();
  });
});
