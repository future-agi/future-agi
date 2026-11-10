import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import ProviderConfigView from "./ProviderConfigView";

vi.mock("./hooks/useGatewayConfig", () => ({
  useRemoveProvider: () => ({ mutate: vi.fn(), isPending: false }),
}));

// The edit dialog has its own suite; only the cards are under test here.
vi.mock("./AddProviderDialog", () => ({
  default: () => null,
}));

// Find the provider's own details by its heading and label, regardless of
// whether the surrounding design-system panel uses Card or Section markup.
const formatShownFor = (providerName) => {
  let panel = screen.getByRole("heading", { name: providerName }).parentElement;
  while (panel && !within(panel).queryByText("API Format", { exact: true })) {
    panel = panel.parentElement;
  }
  if (!panel) throw new Error(`API Format missing for ${providerName}`);
  const label = within(panel).getByText("API Format", { exact: true });
  return label.nextElementSibling.textContent;
};

describe("ProviderConfigView API format", () => {
  it("labels System One providers, and the typesafe alias, as decision models", () => {
    render(
      <ProviderConfigView
        gatewayId="gw-1"
        config={{
          providers: {
            typesafe: {
              base_url: "https://api.typesafe.ai",
              api_format: "systemone",
              models: ["jev-1.13.0"],
            },
            "custom-systemone": {
              base_url: "https://decider.example.com",
              apiFormat: "typesafe",
              models: ["strands-decider"],
            },
          },
        }}
      />,
    );

    expect(formatShownFor("typesafe")).toBe("System One (decision models)");
    expect(formatShownFor("custom-systemone")).toBe(
      "System One (decision models)",
    );
  });

  it("leaves chat formats as their IDs, and a missing one as a dash", () => {
    render(
      <ProviderConfigView
        gatewayId="gw-1"
        config={{
          providers: {
            openai: {
              base_url: "https://api.openai.com/v1",
              api_format: "openai",
            },
            legacy: { base_url: "https://legacy.example.com" },
          },
        }}
      />,
    );

    expect(formatShownFor("openai")).toBe("openai");
    expect(formatShownFor("legacy")).toBe("\u2014");
  });
});
