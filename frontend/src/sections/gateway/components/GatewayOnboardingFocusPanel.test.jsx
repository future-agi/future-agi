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

    expect(screen.getByText("Gateway setup")).toBeVisible();
    expect(screen.getByText("Step 3 of 3")).toBeVisible();
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

  it("uses the first incomplete step when current step copy is omitted", () => {
    render(
      <GatewayOnboardingFocusPanel
        description="Add a policy for the reviewed gateway request."
        steps={[
          { label: "Provider", complete: true },
          { label: "API key", complete: true },
          { label: "Request", complete: true },
          { label: "Policy", complete: false },
        ]}
        title="Add gateway control"
      />,
    );

    expect(screen.getByText("Step 4 of 4")).toBeVisible();
  });

  it("hides secondary actions in single-action focus mode", () => {
    render(
      <GatewayOnboardingFocusPanel
        singleActionFocus
        currentStep="Request"
        description="Send one request."
        primaryAction={{ label: "Send request", onClick: vi.fn() }}
        secondaryAction={{ label: "Open logs", onClick: vi.fn() }}
        steps={[{ label: "Request", complete: false }]}
        title="Send the first gateway request"
      />,
    );

    expect(screen.getByRole("button", { name: /send request/i })).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /open logs/i }),
    ).not.toBeInTheDocument();
  });
});
