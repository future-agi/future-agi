import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ModelDatabaseConfigTab from "./ModelDatabaseConfigTab";
import RoutingConfigTab from "./RoutingConfigTab";

// On a deployment that offers no managed models, the Org config tabs explain
// routing and prices with no managed sentence: there is no managed model to
// fall back to, and no managed catalog price. Where managed models are
// offered the sentences stay, as these tabs' own tests pin.

vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => ["gpt-4o"],
}));

describe("Org config tabs with no managed models", () => {
  it("Routing says where fallbacks go, naming no managed model", () => {
    render(
      <RoutingConfigTab
        routing={{}}
        onChange={vi.fn()}
        managedOffered={false}
      />,
    );
    const note = screen.getByText(
      /Until you turn it on or off, fallback chains run/,
    );
    expect(note).toHaveTextContent(
      "For your organization's API keys, strategy, retry, provider failover, circuit breakers and model fallbacks use only your own providers.",
    );
    expect(note).not.toHaveTextContent(/managed/i);
  });

  it("Model Database states limits and prices, naming no managed model", () => {
    render(
      <ModelDatabaseConfigTab
        modelDatabase={{ overrides: { "gpt-4o": {} } }}
        onChange={vi.fn()}
        managedOffered={false}
      />,
    );
    const intro = screen.getByText(
      /merged on top of the gateway's model database/,
    );
    expect(intro).toHaveTextContent(
      "Prices: a Cost Tracking custom price wins over these.",
    );
    expect(intro).not.toHaveTextContent(/managed/i);
    const limits = screen.getByText(
      /one that sets no max_tokens is sent with that limit/,
    );
    expect(limits).not.toHaveTextContent(/managed/i);
    const prices = screen.getByText(
      /Prices calls made with your own provider keys/,
    );
    expect(prices).not.toHaveTextContent(/managed/i);
  });
});
