import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
import GatewayOnboardingFocusPanel from "./GatewayOnboardingFocusPanel";

describe("GatewayOnboardingFocusPanel", () => {
  it("does not render when hidden", () => {
    render(
      <GatewayOnboardingFocusPanel
        hidden
        description="Hidden description"
        title="Hidden title"
      />,
    );

    expect(screen.queryByTestId("gateway-onboarding-focus")).toBeNull();
  });

  it("renders gateway setup steps and actions", async () => {
    const onPrimary = vi.fn();
    const onSecondary = vi.fn();

    render(
      <GatewayOnboardingFocusPanel
        currentStep="Request"
        description="Send one request and review the first gateway log."
        primaryAction={{ label: "Open request docs", onClick: onPrimary }}
        secondaryAction={{ label: "Copy endpoint", onClick: onSecondary }}
        steps={[
          { label: "Provider", complete: true },
          { label: "API key", complete: true },
          { label: "Request", complete: false },
        ]}
        title="Send the first gateway request"
      />,
    );

    expect(screen.getByText("Gateway onboarding")).toBeVisible();
    expect(screen.getByText("Send the first gateway request")).toBeVisible();
    expect(screen.getByText("Provider")).toBeVisible();
    expect(screen.getByText("API key")).toBeVisible();
    expect(screen.getAllByText("Request").length).toBeGreaterThan(0);

    await userEvent.click(
      screen.getByRole("button", { name: /copy endpoint/i }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /open request docs/i }),
    );

    expect(onSecondary).toHaveBeenCalledTimes(1);
    expect(onPrimary).toHaveBeenCalledTimes(1);
  });

  it("shows a blocker chip when provided", () => {
    render(
      <GatewayOnboardingFocusPanel
        blocker="Needs provider"
        currentStep="Provider"
        description="Add a provider first."
        title="Connect a gateway provider"
      />,
    );

    expect(screen.getByText("Needs provider")).toBeVisible();
  });
});
