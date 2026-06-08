import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithRouter } from "src/utils/test-utils";
import ProjectFtux from "./ProjectFtux";

vi.mock("./NewProject/NewObserve", () => ({
  default: ({ setupVerification, showFirstTraceGuide }) => (
    <div>
      <div>Observe setup instructions</div>
      {showFirstTraceGuide ? <div>First trace guide enabled</div> : null}
      {setupVerification ? (
        <div>
          <div>{setupVerification.title}</div>
          <div>{setupVerification.description}</div>
        </div>
      ) : null}
    </div>
  ),
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
          currentStep: "Choose package",
          description:
            "Choose the package your app uses, paste the matching setup, run one request, wait for the trace, review it, then create the first quality check.",
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
        observeSetupVerification={{
          description:
            "Keep this page open after running your app. We check every few seconds and move you forward when data arrives.",
          status: "waiting",
          title: "Checking for your first trace",
        }}
      />,
      { route: "/dashboard/observe?setup=true&source=onboarding" },
    );

    expect(screen.getByText("Connect Observe to your app")).toBeVisible();
    expect(screen.getByText("Connect your app")).toBeVisible();
    expect(
      screen.getByText(
        "Choose the package your app uses, paste the matching setup, then run one request. Future AGI waits for the trace, opens review, then guides the first quality check.",
      ),
    ).toBeVisible();
    expect(screen.queryByText(/evaluator setup/i)).not.toBeInTheDocument();
    expect(screen.getByText("Install")).toBeVisible();
    expect(screen.getByText("Trace")).toBeVisible();
    expect(screen.getByText("Review")).toBeVisible();
    expect(screen.getByText("Observe setup instructions")).toBeVisible();
    expect(screen.getByText("First trace guide enabled")).toBeVisible();
    expect(screen.getByText("Checking for your first trace")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /open sample trace/i }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /review setup/i }));

    expect(onPrimaryAction).toHaveBeenCalledTimes(1);
  });

  it("does not show observe setup focus copy on prototype FTUX", () => {
    renderWithRouter(
      <ProjectFtux
        observeSetupCopy={{
          currentStep: "Choose package",
          description:
            "Choose the package your app uses, paste the matching setup, run one request, wait for the trace, review it, then create the first quality check.",
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
