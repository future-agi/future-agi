// Org Config > Providers offers no per-org override: it says where a
// provider is edited, links there, and shows each provider's status with
// its reason.
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import ProviderConfigTab from "./ProviderConfigTab";

let mockProviders = [];

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useProviderHealth: () => ({ data: { providers: mockProviders } }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gateway-1" }),
}));

describe("Org Config > Providers", () => {
  it("promises no override it cannot make and links to the real edit dialog", () => {
    mockProviders = [
      {
        id: "openai",
        name: "openai",
        status: "healthy",
        healthy: true,
        pushed: true,
        status_reason: "fewer than 5 requests in 24 h",
        models: ["gpt-4o"],
      },
    ];
    render(<ProviderConfigTab />);

    expect(screen.queryByText(/per-org overrides/)).toBeNull();
    const link = screen.getByRole("link", {
      name: "Providers > Provider Config",
    });
    expect(link).toHaveAttribute("href", "/dashboard/gateway/providers/config");
    expect(screen.getByText(/Edit button/)).toBeInTheDocument();
  });

  it("shows each provider's status and why", () => {
    mockProviders = [
      {
        id: "openai",
        name: "openai",
        status: "degraded",
        healthy: true,
        pushed: true,
        status_reason:
          "20.0% of 10 requests failed in 24 h (degraded at 10% or more)",
        models: ["gpt-4o"],
      },
      {
        id: "groq",
        name: "groq",
        status: "unhealthy",
        healthy: false,
        pushed: false,
        status_reason: "not pushed: unsupported credential key organization",
        models: ["llama-3.1-8b"],
      },
    ];
    render(<ProviderConfigTab />);

    const openai = within(screen.getByText("openai").closest(".MuiCard-root"));
    expect(openai.getByText("degraded")).toBeInTheDocument();
    expect(
      openai.getByText(
        "20.0% of 10 requests failed in 24 h (degraded at 10% or more)",
      ),
    ).toBeInTheDocument();
    const groq = within(screen.getByText("groq").closest(".MuiCard-root"));
    expect(groq.getByText("not pushed")).toBeInTheDocument();
    expect(
      groq.getByText("not pushed: unsupported credential key organization"),
    ).toBeInTheDocument();
  });
});
