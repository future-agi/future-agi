import React from "react";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";

import FallbacksSection from "../FallbacksSection";

let hookReturn;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("../hooks/useFallbackConfig", () => ({
  useFallbackConfig: () => hookReturn,
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "default" }),
}));
vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useProviderHealth: () => ({
    data: {
      providers: [{ name: "openai", models: ["gpt-4o", "gpt-4o-mini"] }],
    },
  }),
}));

const CHAINS = { "gpt-4o": ["gpt-4o-mini"] };

function renderPage(routing) {
  hookReturn = {
    routing,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
    saveRouting: vi.fn(),
    isSaving: false,
  };
  return render(
    <HelmetProvider>
      <FallbacksSection />
    </HelmetProvider>,
  );
}

const chainsSwitch = () =>
  within(screen.getByTestId("fallbacks-section-chains")).getAllByRole(
    "checkbox",
  )[0];

describe("Fallbacks page", () => {
  beforeEach(() => {
    hookReturn = null;
  });

  it("is a gateway page with an 18 px title", () => {
    renderPage({ fallback_enabled: true, model_fallbacks: CHAINS });
    expect(
      screen.getByRole("heading", { level: 1, name: "Fallbacks" }),
    ).toBeInTheDocument();
  });

  // fallbackSwitches.js: with fallback_enabled unset the gateway runs
  // the org's chains whenever one exists, and never when it is false.
  it("shows the chains switch ON when nothing was saved and a chain exists, as the gateway runs it", () => {
    renderPage({ model_fallbacks: CHAINS });
    expect(chainsSwitch()).toBeChecked();
    expect(screen.queryByText(/saved but not tried/i)).toBeNull();
  });

  it("shows the chains switch OFF when it was turned off, and says the chains are not tried", () => {
    renderPage({ fallback_enabled: false, model_fallbacks: CHAINS });
    expect(chainsSwitch()).not.toBeChecked();
    expect(
      screen.getByText(/saved but not tried: fallbacks are off/i),
    ).toBeInTheDocument();
  });

  // Provider failover runs only when failover.enabled is on; the chains
  // switch does not turn it on.
  it("shows provider failover OFF when it was never turned on", () => {
    renderPage({ fallback_enabled: true, model_fallbacks: CHAINS });
    expect(
      screen.getByRole("checkbox", { name: "Provider failover on" }),
    ).not.toBeChecked();
  });

  it("says nothing extra when the chains are on", () => {
    renderPage({ fallback_enabled: true, model_fallbacks: CHAINS });
    expect(chainsSwitch()).toBeChecked();
    expect(screen.queryByText(/saved but not tried/i)).toBeNull();
  });

  it("shows one error state with Retry when the config cannot be read", () => {
    hookReturn = null;
    render(
      <HelmetProvider>
        <div />
      </HelmetProvider>,
    );
    hookReturn = {
      routing: {},
      isLoading: false,
      error: new Error("Network error"),
      refetch: vi.fn(),
      saveRouting: vi.fn(),
      isSaving: false,
    };
    render(
      <HelmetProvider>
        <FallbacksSection />
      </HelmetProvider>,
    );
    expect(
      screen.getByText("Couldn't load the fallback config"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
