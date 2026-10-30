import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ProviderConfigView from "./ProviderConfigView";

vi.mock("./hooks/useGatewayConfig", () => ({
  useRemoveProvider: () => ({ mutate: vi.fn(), isPending: false }),
}));

// The edit dialog has its own suite; only the cards are under test here.
vi.mock("./AddProviderDialog", () => ({
  default: () => null,
}));

// The value printed beside a card's "API Format" label.
const formatShownFor = (providerName) => {
  const card = screen
    .getByRole("heading", { name: providerName })
    .closest(".MuiCard-root");
  const label = Array.from(card.querySelectorAll("p")).find(
    (el) => el.textContent === "API Format",
  );
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
